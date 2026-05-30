import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from openai import AuthenticationError, PermissionDeniedError, RateLimitError
from openai import APIError, APITimeoutError, APIConnectionError

from .config import Config

logger = logging.getLogger(__name__)

BILINGUAL_SYSTEM_PROMPT = """You are a professional translator. Translate the following English text into Simplified Chinese.
For each text segment separated by the delimiter '|||', return only the Chinese translation.

Important rules:
1. Preserve all HTML tags and entities exactly as they appear
2. Do not translate proper nouns that should stay in English (brand names, URLs, code)
3. Preserve numbers, dates, and punctuation structure
4. Return each translation segment separated by '|||' in the same order
5. Do not add any extra commentary or explanation
6. For source code: keep all code and syntax completely unchanged, translate ONLY the comments within the code"""

CHINESE_ONLY_SYSTEM_PROMPT = """You are a professional translator. Translate the following English text into Simplified Chinese.
For each text segment separated by the delimiter '|||', return only the Chinese translation.

Important rules:
1. Preserve all HTML tags and entities exactly as they appear
2. Do not translate proper nouns that should stay in English (brand names, URLs, code)
3. Preserve numbers, dates, and punctuation structure
4. Return each translation segment separated by '|||' in the same order
5. Do not add any extra commentary or explanation
6. For source code: keep all code and syntax completely unchanged, translate ONLY the comments within the code"""

TRANSLATION_DELIMITER = "|||"


class Translator:
    def __init__(self, config: Config):
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.api_base,
            timeout=config.api_timeout,
            max_retries=0,  # we handle retries ourselves
        )

    def _get_system_prompt(self) -> str:
        if self._config.translation_mode == "bilingual":
            return BILINGUAL_SYSTEM_PROMPT
        return CHINESE_ONLY_SYSTEM_PROMPT

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Translate a batch of text strings. Returns translations in the same order."""
        if not texts:
            return []

        prompt = TRANSLATION_DELIMITER.join(texts)
        system_prompt = self._get_system_prompt()

        for attempt in range(self._config.max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self._config.model,
                    messages=[
                        {"role": "user", "content": f"{system_prompt}\n\nText to translate:\n{prompt}"},
                    ],
                    temperature=self._config.temperature,
                )
                content = response.choices[0].message.content
                translations = content.split(TRANSLATION_DELIMITER)
                translations = [t.strip() for t in translations]

                if len(translations) != len(texts):
                    if attempt < self._config.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    while len(translations) < len(texts):
                        translations.append(texts[len(translations)])
                    translations = translations[: len(texts)]

                return translations

            except AuthenticationError as e:
                raise RuntimeError(
                    "API Key is invalid. Please check your API Key in settings."
                ) from e
            except PermissionDeniedError as e:
                raise RuntimeError(
                    "Permission denied. Your API Key may not have access to this model."
                ) from e
            except RateLimitError as e:
                if attempt < self._config.max_retries - 1:
                    wait = 2 ** attempt * 5
                    logger.warning("Rate limited, retrying in %ds...", wait)
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    "Rate limit exceeded. Please wait and try again later."
                ) from e
            except APITimeoutError as e:
                if attempt < self._config.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Timeout, retrying in %ds...", wait)
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    "API request timed out. Please check your network connection."
                ) from e
            except APIConnectionError as e:
                if attempt < self._config.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Connection error, retrying in %ds...", wait)
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    "Cannot connect to API. Please check your network and API Base URL."
                ) from e
            except APIError as e:
                # Check for common error messages
                msg = str(e)
                if "insufficient" in msg.lower() or "balance" in msg.lower() or "quota" in msg.lower():
                    raise RuntimeError(
                        "Insufficient balance. Please recharge your API account."
                    ) from e
                if "model" in msg.lower() and ("not found" in msg.lower() or "not exist" in msg.lower()):
                    raise RuntimeError(
                        f"Model '{self._config.model}' not found. Please check the model name in settings."
                    ) from e
                if attempt < self._config.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "API error (attempt %d/%d): %s. Retrying in %ds...",
                        attempt + 1, self._config.max_retries, e, wait,
                    )
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"API error: {e}") from e
            except Exception as e:
                if attempt < self._config.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Translation attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt + 1, self._config.max_retries, e, wait,
                    )
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"Translation failed: {e}"
                    ) from e

        return texts

    def translate_all(self, texts: list[str], progress_callback=None) -> list[str]:
        """Translate all texts using concurrent batch requests.

        Splits texts into batches and sends them in parallel via ThreadPoolExecutor.
        """
        if not texts:
            return []

        batch_size = self._config.batch_size
        max_workers = self._config.max_concurrency

        # Split into batches, each with an index for ordering
        batches = []
        for i in range(0, len(texts), batch_size):
            batches.append((i, texts[i : i + batch_size]))

        results = {}  # batch_idx -> translations

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_batch = {}
            for batch_idx, batch_texts in batches:
                future = executor.submit(self.translate_batch, batch_texts)
                future_to_batch[future] = batch_idx

            for future in as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                try:
                    results[batch_idx] = future.result()
                except Exception as e:
                    logger.error("Batch %d failed: %s", batch_idx // batch_size + 1, e)
                    raise

                if progress_callback:
                    progress_callback(len(results) * batch_size)

        # Reassemble in order
        all_translations = []
        for i in range(0, len(texts), batch_size):
            batch_translations = results.get(i, texts[i : i + batch_size])
            all_translations.extend(batch_translations)

        return all_translations
