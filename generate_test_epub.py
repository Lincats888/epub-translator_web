#!/usr/bin/env python3
"""Generate a minimal test EPUB file for testing the EPUB Translator."""

import zipfile
import os

OUTPUT_DIR = "tests/fixtures"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "sample.epub")


def create_minimal_epub():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    mimetype = "application/epub+zip"

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" unique-identifier="bookid" xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book Title</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="bookid">urn:uuid:sample-1234-5678</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter1" href="chapter1.html" media-type="application/xhtml+xml"/>
    <item id="chapter2" href="chapter2.html" media-type="application/xhtml+xml"/>
    <item id="image1" href="images/test.png" media-type="image/png"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter1"/>
    <itemref idref="chapter2"/>
  </spine>
</package>"""

    toc_ncx = """<?xml version="1.0" encoding="UTF-8"?>
<ncx version="2005-1" xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <head>
    <meta name="dtb:uid" content="urn:uuid:sample-1234-5678"/>
  </head>
  <docTitle>
    <text>Test Book Title</text>
  </docTitle>
  <navMap>
    <navPoint id="nav1" playOrder="1">
      <navLabel><text>Chapter 1 - Getting Started</text></navLabel>
      <content src="chapter1.html"/>
    </navPoint>
    <navPoint id="nav2" playOrder="2">
      <navLabel><text>Chapter 2 - Advanced Topics</text></navLabel>
      <content src="chapter2.html"/>
    </navPoint>
  </navMap>
</ncx>"""

    chapter1_html = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 1 - Getting Started</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1>Chapter 1 - Getting Started</h1>
  <p>This is the first paragraph of the book. It contains some meaningful English text that should be translated into Chinese.</p>
  <p>Here is another paragraph with a <a href="chapter2.html">link to Chapter 2</a> inside it.</p>
  <p>This paragraph has <strong>bold text</strong> and <em>italic text</em> that must be preserved.</p>
  <div class="image-container">
    <img src="images/test.png" alt="A test image description"/>
    <p class="caption">Figure 1: A sample illustration</p>
  </div>
  <script type="text/javascript">
    console.log("This should NOT be translated");
  </script>
  <style>
    /* This should NOT be translated either */
    body { font-family: serif; }
  </style>
</body>
</html>"""

    chapter2_html = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 2 - Advanced Topics</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1>Chapter 2 - Advanced Topics</h1>
  <p>This chapter covers more complex subjects that require careful attention. We will explore several key concepts in detail.</p>
  <p>Understanding these principles is essential for building robust applications. Each section builds upon the previous one.</p>

  <h2>Comparison Table</h2>
  <p>The following table summarizes the main differences between the approaches:</p>
  <table>
    <tr><th>Feature</th><th>Approach A</th><th>Approach B</th></tr>
    <tr><td>Performance</td><td>Fast and efficient</td><td>Moderate speed</td></tr>
    <tr><td>Scalability</td><td>Horizontal scaling</td><td>Vertical scaling</td></tr>
    <tr><td>Complexity</td><td>Simple to implement</td><td>Requires expertise</td></tr>
    <tr><td>Cost</td><td>Low initial investment</td><td>Higher upfront cost</td></tr>
  </table>

  <p>As you can see from the table above, each approach has its own strengths and weaknesses.</p>

  <h2>Code Example</h2>
  <p>Here is a sample implementation demonstrating the core concept:</p>
  <pre><code>def calculate_fibonacci(n):
    # Base case: return n for small values
    if n <= 1:
        return n
    # Recursive case: sum of previous two
    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)

# Print the first 10 Fibonacci numbers
for i in range(10):
    print(calculate_fibonacci(i))</code></pre>

  <p>The algorithm above uses recursion, which is elegant but not always the most efficient approach for large inputs.</p>

  <ul>
    <li>First important point: always consider time complexity</li>
    <li>Second critical insight: space complexity matters too</li>
    <li>Third key takeaway: choose the right data structure</li>
  </ul>

  <p>In conclusion, this chapter has given you a solid understanding of the fundamental concepts. Practice these techniques to master them.</p>
</body>
</html>"""

    style_css = "body { font-family: serif; margin: 1em; }\nh1 { color: #333; }"

    # Create a 1x1 pixel PNG
    # Minimal valid PNG
    png_data = (
        b"\x89PNG\r\n\x1a\n"  # PNG signature
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with zipfile.ZipFile(OUTPUT_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype MUST be first and STORED (uncompressed)
        zf.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)
        zf.writestr("OEBPS/chapter1.html", chapter1_html)
        zf.writestr("OEBPS/chapter2.html", chapter2_html)
        zf.writestr("OEBPS/images/test.png", png_data)
        zf.writestr("OEBPS/style.css", style_css)

    print(f"Test EPUB created: {OUTPUT_PATH}")


if __name__ == "__main__":
    create_minimal_epub()
