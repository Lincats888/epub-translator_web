#!/usr/bin/env python3
"""EPUB Translator - 基于 Python + DeepSeek 的 EPUB 翻译工具

Usage:
    python main.py <epub_file>              # Translate an EPUB
    python main.py <epub_file> --config config.yaml
    python main.py rebuild <book_name>       # Rebuild EPUB from temp/ (no re-translation)
"""

import argparse
import os
import sys
import time

from epub_translator.config import Config
from epub_translator.extractor import EpubExtractor
from epub_translator.cache import TranslationCache
from epub_translator.parser import parse_file
from epub_translator.translator import Translator
from epub_translator.rebuilder import inject_line_height, rebuild_epub


def cmd_translate(args):
    if not os.path.exists(args.epub_file):
        print(f"Error: EPUB file not found: {args.epub_file}")
        sys.exit(1)

    # 1. Load config
    print("[1/5] Loading configuration...")
    config = Config(args.config)
    config.load()
    if not config.api_key or config.api_key == "sk-xxxx":
        print("Error: API key not configured. Please set 'api_key' in config.yaml")
        sys.exit(1)

    # 2. Extract EPUB
    print("[2/5] Extracting EPUB...")
    extractor = EpubExtractor(args.epub_file, args.temp_dir)
    extract_dir = extractor.extract()
    book_name = os.path.splitext(os.path.basename(args.epub_file))[0]
    print(f"       Extracted to: {extract_dir}")

    # 3. Load translation cache
    print("[3/5] Loading translation cache...")
    cache = TranslationCache(extract_dir)
    cache.load()

    # 4. Collect content files (HTML, NCX, OPF)
    content_files = extractor.list_content_files()
    toc_file = extractor.find_toc_file()
    if toc_file and toc_file not in content_files:
        content_files.append(toc_file)
    opf_file = extractor.get_opf_path()
    if opf_file not in content_files:
        content_files.append(opf_file)

    if not content_files:
        print("       No content files found in EPUB.")
        sys.exit(1)

    # 5. Translate file by file
    print(f"[4/5] Translating {len(content_files)} documents...")
    translator = Translator(config)
    total_translated = 0
    total_cached = 0
    overall_start = time.time()
    is_bilingual = config.translation_mode == "bilingual"

    def _print_progress(label, current, total, elapsed):
        """Print a single-line progress bar that overwrites itself."""
        pct = current / total if total else 1
        bar_len = 30
        filled = int(bar_len * pct)
        bar = "#" * filled + "-" * (bar_len - filled)
        line = f"\r  {label} |{bar}| {current}/{total} [{elapsed:.0f}s]"
        sys.stdout.write(line)
        sys.stdout.flush()

    for file_idx, file_path in enumerate(content_files, 1):
        rel_path = os.path.relpath(file_path, extract_dir)

        # Parse file
        parsed = parse_file(file_path, config.skip_tags, bilingual=is_bilingual)
        if not parsed.fragments:
            continue

        # Check cache
        uncached_indices = []
        uncached_texts = []
        for i, frag in enumerate(parsed.fragments):
            cached = cache.get(frag.text)
            if cached is not None:
                total_cached += 1
            else:
                uncached_texts.append(frag.text)
                uncached_indices.append(i)

        if uncached_texts:
            file_start = time.time()
            short_name = rel_path[:35] + "..." if len(rel_path) > 35 else rel_path
            label = f"[{file_idx}/{len(content_files)}] {short_name}"

            progress_state = {"count": 0, "shown": False}

            def on_batch_complete(completed_count, _total=len(uncached_texts), _start=file_start):
                count = min(completed_count, _total)
                progress_state["count"] = count
                if count < _total:
                    elapsed = time.time() - _start
                    _print_progress(label, count, _total, elapsed)
                    progress_state["shown"] = True

            translations_result = translator.translate_all(
                uncached_texts, progress_callback=on_batch_complete
            )

            # Build full translations list for this file
            file_translations = [None] * len(parsed.fragments)
            for i, frag in enumerate(parsed.fragments):
                cached = cache.get(frag.text)
                if cached is not None:
                    file_translations[i] = cached
            for idx, translation in zip(uncached_indices, translations_result):
                file_translations[idx] = translation
                cache.put(parsed.fragments[idx].text, translation)
                total_translated += 1

            cache.flush()
            file_elapsed = time.time() - file_start
            if progress_state["shown"]:
                sys.stdout.write("\r" + " " * 80 + "\r")
            print(f"  [{file_idx}/{len(content_files)}] {rel_path}  done ({file_elapsed:.1f}s)")
        else:
            file_translations = [cache.get(frag.text) for frag in parsed.fragments]

        # Write back and save
        parsed.save(file_translations)

    elapsed = time.time() - overall_start
    print(f"\n[5/5] Rebuilding EPUB...")
    if is_bilingual:
        inject_line_height(extract_dir)
        print(f"       Injected line-height CSS for Chinese readability")
    output_path = rebuild_epub(extract_dir, args.output_dir, book_name)

    print(f"\n{'='*50}")
    print(f"Translation complete! ({elapsed:.1f}s)")
    print(f"  Fragments translated: {total_translated}")
    print(f"  Fragments from cache: {total_cached}")
    print(f"  Output: {output_path}")


def cmd_rebuild(args):
    book_name = args.book_name
    extract_dir = os.path.join(args.temp_dir, book_name)

    if not os.path.isdir(extract_dir):
        print(f"Error: temp directory not found: {extract_dir}")
        print(f"       Run 'python main.py <epub_file>' first to extract and translate.")
        sys.exit(1)

    # Load config for line-height injection decision
    config = Config(args.config)
    config.load()
    is_bilingual = config.translation_mode == "bilingual"

    if is_bilingual:
        print("Injecting line-height CSS for Chinese readability...")
        inject_line_height(extract_dir)

    print(f"Rebuilding EPUB from: {extract_dir}")
    output_path = rebuild_epub(extract_dir, args.output_dir, book_name)

    print(f"\n{'='*50}")
    print(f"Rebuild complete!")
    print(f"  Output: {output_path}")


def main():
    # Backward compatibility: python main.py <epub_file> → python main.py translate <epub_file>
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-") and argv[0] not in ("translate", "rebuild", "-h", "--help"):
        argv = ["translate"] + argv

    parser = argparse.ArgumentParser(
        description="Translate EPUB books using DeepSeek API"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # translate (default when first arg is not a subcommand)
    translate_parser = subparsers.add_parser("translate", help="Translate an EPUB file")
    translate_parser.add_argument("epub_file", help="Path to the EPUB file to translate")

    # rebuild
    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild EPUB from temp/ without re-translating")
    rebuild_parser.add_argument("book_name", help="Book directory name inside temp/ (e.g. 'My Book')")

    # Shared options
    for sp in (translate_parser, rebuild_parser):
        sp.add_argument(
            "--config", default="config.yaml", help="Path to config file (default: config.yaml)"
        )
        sp.add_argument(
            "--temp-dir", default="temp", help="Temporary extraction directory (default: temp)"
        )
        sp.add_argument(
            "--output-dir", default="output", help="Output directory (default: output)"
        )

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "translate":
        cmd_translate(args)
    elif args.command == "rebuild":
        cmd_rebuild(args)


if __name__ == "__main__":
    main()
