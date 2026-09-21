# Benchmark receipt summary tables

Extracted programmatically from the JSON receipts in this directory (single source of truth; the README prose cites these). Aggregate sustained-decode tok/s by concurrency x context; run parameters in each file's `metadata` and the README "Benchmark setup" section.

## glm-5.3-flash - 0.4.29 harness, 2026-09-02

File: `glm53-tp2-2026-09-02.json` (engine endpoint in `metadata.server`, parameterised)

| Context | c1 | c2 | c4 |
|---|---|---|---|
| 0 (short) | 139.7 | 230.2 | 348.8 |
| 16384 | 144.8 | 216.2 | 332.6 |
| 32768 | 152.0 | 212.0 | 337.0 |
| 65536 | 148.2 | 222.0 | 346.8 |
| 131072 | 131.7 | 210.8 | - |

Range across contexts: c1: 131.7 to 152.0; c2: 210.8 to 230.2; c4: 332.6 to 348.8 tok/s.

## glm-5.3-flash - 0.4.34 harness, 2026-09-03

File: `glm53-tp2-2026-09-03.json` (engine endpoint in `metadata.server`, parameterised)

| Context | c1 | c2 | c4 |
|---|---|---|---|
| 0 (short) | 180.3 | 272.0 | 371.5 |
| 16384 | 172.9 | 251.8 | 391.3 |
| 32768 | 167.9 | 249.3 | 378.7 |
| 65536 | 176.8 | - | - |
| 131072 | 167.9 | - | - |

Range across contexts: c1: 167.9 to 180.3; c2: 249.3 to 272.0; c4: 371.5 to 391.3 tok/s.

## deepseek-v4-flash - 0.4.29 harness, 2026-08-28

File: `deepseek-v4-flash-tp2.json` (engine endpoint in `metadata.server`, parameterised)

| Context | c1 | c2 | c4 |
|---|---|---|---|
| 0 (short) | 173.5 | 269.0 | 389.6 |
| 16384 | 186.9 | 280.6 | - |
| 32768 | 187.2 | - | - |
| 65536 | 184.4 | - | - |
| 131072 | 185.6 | - | - |

Range across contexts: c1: 173.5 to 187.2; c2: 269.0 to 280.6; c4: 389.6 to 389.6 tok/s.

## Qwen3.6-35B-A3B-NVFP4 - 0.4.29 harness, 2026-09-01

File: `qwen36-35b-a3b-nvfp4.json` (engine endpoint in `metadata.server`, parameterised)

| Context | c1 | c2 | c4 |
|---|---|---|---|
| 0 (short) | 263.9 | 409.0 | 770.2 |
| 16384 | 256.1 | 397.1 | 714.7 |
| 32768 | 245.4 | - | - |
| 65536 | 230.2 | - | - |
| 131072 | 197.6 | - | - |

Range across contexts: c1: 197.6 to 263.9; c2: 397.1 to 409.0; c4: 714.7 to 770.2 tok/s.

## Prefill (integrated decode scout, client-measured)

| Model (run) | Prompt | tok/s | TTFT |
|---|---|---|---|
| glm-5.3-flash (2026-09-02) | 8192 tok | 4941 | 1.08 s |
| glm-5.3-flash (2026-09-02) | 16384 tok | 6013 | 1.76 s |
| glm-5.3-flash (2026-09-02) | 32768 tok | 3678 | 5.71 s |
| glm-5.3-flash (2026-09-02) | 65536 tok | 4255 | 9.83 s |
| glm-5.3-flash (2026-09-02) | 131072 tok | 6229 | 13.41 s |
| glm-5.3-flash (2026-09-03) | 8192 tok | 5232 | 1.02 s |
| glm-5.3-flash (2026-09-03) | 16384 tok | 5782 | 1.83 s |
| glm-5.3-flash (2026-09-03) | 32768 tok | 5705 | 3.68 s |
| glm-5.3-flash (2026-09-03) | 65536 tok | 5542 | 7.55 s |
| glm-5.3-flash (2026-09-03) | 131072 tok | 5912 | 14.13 s |
| deepseek-v4-flash (2026-08-28) | 8192 tok | 5734 | 1.43 s |
| deepseek-v4-flash (2026-08-28) | 16384 tok | 5582 | 2.89 s |
| deepseek-v4-flash (2026-08-28) | 32768 tok | 6363 | 5.04 s |
| deepseek-v4-flash (2026-08-28) | 65536 tok | 6793 | 9.42 s |
| deepseek-v4-flash (2026-08-28) | 131072 tok | 6583 | 19.41 s |
| Qwen3.6-35B-A3B-NVFP4 (2026-09-01) | 8192 tok | 22553 | 0.36 s |
| Qwen3.6-35B-A3B-NVFP4 (2026-09-01) | 16384 tok | 20767 | 0.78 s |
| Qwen3.6-35B-A3B-NVFP4 (2026-09-01) | 32768 tok | 17514 | 1.84 s |
| Qwen3.6-35B-A3B-NVFP4 (2026-09-01) | 65536 tok | 13859 | 4.65 s |
| Qwen3.6-35B-A3B-NVFP4 (2026-09-01) | 131072 tok | 9432 | 13.65 s |

