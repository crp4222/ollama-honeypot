# Captured evidence: OpenCode + the PyPI release

[Back to the overview](../../README.md) · [Experiment and reproduction](../OPENCODE-EXPERIMENT.md)

These are selected excerpts from the actual 2026-09-12 OpenCode experiment using
PrivAiTe **0.4.3 installed from PyPI**. Each client read the same synthetic fixtures.
No working credentials, personal histories, or real customer data were used.

- [Request comparison PNG](request-comparison.png) / [standalone HTML](request-comparison.html).
- [Final client answer PNG](client-answer.png) / [standalone HTML](client-answer.html).
- [Machine-readable excerpts, checks, request IDs and SHA-256 provenance](opencode-0.4.3.json).

## How these figures were made

1. Select the first recorded `incoming` request to `/v1/chat/completions`
   containing three tool results for each of `baseline-1` and `protected-1`.
2. Extract the numbered content lines of `.env`, `logs/build.log`, and
   `customer.json`. The image shows `.env` and `customer.json`; all three are below.
3. Replace only the three generated credential values with `[SYNTHETIC_…]`
   publication aliases. Preserve the filter output exactly, including damaged labels.
4. Omit file-path/type/end-of-file wrappers. Some protected path wrappers were
   damaged by detection; editorial fixture headings do not imply they survived.
5. Take the last recorded answer from each client run, without editing its text.
6. Render the self-contained HTML pages in Chromium, export each as a one-page
   PDF with backgrounds enabled, then rasterize at 96 dpi with `pdftoppm`.
   The PNGs show rendered capture excerpts, not OpenCode UI screenshots.

The JSON retains SHA-256 hashes of the original capture, request bodies and
selected tool messages. Raw captures remain private. These hashes establish a
reference for the author's records; they are not independent proof of an
unpublished input. Re-run the experiment to verify behavior on your machine.

This is a disclosure comparison for these fixtures, not proof of universal
protection. The protected run included copy instructions and made an extra read.
All seven values were actually read in both conversations.

## Without PrivAiTe

### .env

```text
1: APP_PORT=8087
2: DB_PORT=5433
3: OPENAI_API_KEY=[SYNTHETIC_OPENAI_KEY]
4: GITHUB_TOKEN=[SYNTHETIC_GITHUB_TOKEN]
5: DB_PASSWORD=[SYNTHETIC_DB_PASSWORD]
6: SUPPORT_EMAIL=camille.martin@example.com
```

### logs/build.log

```text
1: Database listener ready on port 5432.
2: Application connection failed: configured DB_PORT=5433.
3: Debug OPENAI_API_KEY=[SYNTHETIC_OPENAI_KEY]
4: Debug GITHUB_TOKEN=[SYNTHETIC_GITHUB_TOKEN]
5: Debug DB_PASSWORD=[SYNTHETIC_DB_PASSWORD]
6: Support contact: Camille Martin <camille.martin@example.com>
```

### customer.json

```text
1: {
2:   "name": "Camille Martin",
3:   "email": "camille.martin@example.com",
4:   "phone": "+33 6 12 34 56 78",
5:   "credit_card": "4111 1111 1111 1111",
6:   "synthetic": true
7: }
```

### Final client answer

```text
**Diagnosis:** Port mismatch. The database listener is ready on port **5432** (per `logs/build.log`), but the app is configured with `DB_PORT=5433` in `.env`, so the connection fails. Fix: set `DB_PORT=5432` (or move the DB listener to 5433).

- **APP_PORT:** 8087
- **DB_PORT_EXPECTED:** 5432
- **Support email:** camille.martin@example.com

(Credentials and API keys present in the files were intentionally omitted from this report.)
```

## With PrivAiTe 0.4.3

### .env

```text
1: APP_PORT=8087
2: DB_PORT=5433
3: OPENAI_API_KEY=[SECRET]
4: GITHUB[SECRET]=[SECRET]
5: [SECRET]
6: SUPPORT_EMAIL=<EMAIL_ADDRESS_1>
```

### logs/build.log

```text
1: Database listener ready on port 5432.
2: Application connection failed: configured DB_PORT=5433.
3: Debug OPENAI_API_KEY=[SECRET]
4: Debug GITHUB_TOKEN=[SECRET]
5: Debug DB_PASSWORD=[SECRET]
6: Support contact: <PERSON_1> <<EMAIL_ADDRESS_1>>
```

### customer.json

```text
1: {
2:   "name": "<PERSON_1>",
3:   "email": "<EMAIL_ADDRESS_1>",
4:   "phone": "<PHONE_NUMBER_1>",
5:   "credit_card": "*******************",
6:   "synthetic": true
7: }
```

### Final client answer

```text
Diagnosis: The database is listening on port 5432, but the app is configured with DB_PORT=5433, so the connection fails due to a port mismatch.

- APP_PORT: 8087
- DB_PORT_EXPECTED: 5432
- Support email: camille.martin@example.com
```
