# Purpose

The dashboard shows Markdown only today. The page loads no external resources, and only the escaped Markdown render uses `innerHTML` (`shared/questions/page.ts:1-3`, see [current-state](../../research/synthesis/current-state.md), B2). So an interactive diagram needs a layout done on the server and a canvas built by the inline script.

VR-03 delivers that canvas without breaking the page rules.
