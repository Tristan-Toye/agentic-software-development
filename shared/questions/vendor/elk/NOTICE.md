# elkjs 0.12.1 (vendored)

- Project: https://github.com/kieler/elkjs (Eclipse Layout Kernel compiled to JavaScript)
- Source: `npm pack elkjs@0.12.1`, files taken unchanged from `package/lib/`
- License: EPL-2.0 OR GPL-3.0-or-later (see `LICENSE.md`, the package's own file)
- Used by: `shared/questions/vrlayout.ts` (server-side layout) and `GET /vendor/elk/elk-worker.min.js` (the same worker file from the dashboard origin; the page does not use it today because the server lays out)
- Never import `elk.bundled.js`: under Bun it starts a Worker it cannot terminate and the process never exits (research/d0/REPORT.md, 1.2). Use `elk-api.js` with `elk-worker.min.js` as the worker.
- No package.json: the two files are the whole dependency. To upgrade, pack the new version, replace both files, update this table and run `bun test shared/test/vrlayout.test.ts`.

| File | Bytes | sha256 |
|---|---|---|
| `elk-api.js` | 9782 | ccca46175c05bbd280b5de6c9853103711e52c666a101f145b717e345363e24d |
| `elk-worker.min.js` | 1631868 | dca3a19d9ec37f5634bf5876b4ebd2455c3f43f0d32570136c13c13edaba9bfb |
| `LICENSE.md` | 14.1 KB | 637e81f4a1b6b4079535c499fca05e238cf7605c1ff5b76d60d7da7ce96700c9 |
