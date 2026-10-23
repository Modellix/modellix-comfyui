# Compatibility and acceptance

Acceptance date: **2026-10-16**. This records observed coverage, rather than claiming every catalog model has been generated.

## Tested environment

| Component | Observed version |
| --- | --- |
| OS | Windows, CPU-only isolated test host |
| ComfyUI | v0.40.0, commit `3541246a9c997f4b8c32f9f88f3c985d02021c79` |
| Frontend | 1.55.17 |
| Python | 3.11.9 |
| torch | 2.14.1+cpu |
| PyAV | 18.1.0 |
| NumPy / Pillow | 2.4.6 / 12.3.0 |

Python >=3.10 and ComfyUI >=0.40.0 are the declared baseline. The CI matrix separately checks pure Python installation/lint/unit tests on Windows and Linux with Python 3.10/3.12, plus a real Linux CPU ComfyUI host job. Do not interpret that configuration as a completed test on every OS or future ComfyUI version. Windows Credential Manager was tested with actual save, a fresh process reading and authenticating, and deletion. macOS/Linux secure backends are adapted but were not tested against a real desktop keychain in this local acceptance run; unavailable/insecure backends have explicit failure tests.

## Live catalog and schemas

The final authenticated production catalog contained **172 models** across eight raw types. All 172 public schema URLs were rechecked: **172 valid request schemas**, no missing in-catalog schemas. The readable media-output set contains **167 models**. All current readable schemas parse with the package's bounded parser. That is schema coverage, not 167 paid generations.

| Raw model type | Catalog count | Package behavior |
| --- | ---: | --- |
| text-to-image | 21 | Native IMAGE results |
| image-to-image | 24 | Native IMAGE input/output; media references follow each schema |
| text-to-video | 27 | Native VIDEO results |
| image-to-video | 60 | Frame/reference branches and native image inputs |
| video-to-video | 24 | Native VIDEO input/output and optional references |
| text-to-speech | 9 | Native AUDIO results |
| speech-to-speech | 2 | Native AUDIO reference and result |
| speech-to-text | 5 | Listed with an unsupported-output explanation; text is outside this contract |

During initial acceptance the catalog had 174 entries, including `vidu/vidu-q2-ns-i2v` and `vidu/vidu-new-ns-i2v` with missing schemas. The service subsequently removed those two invalid entries. The final catalog/schema sweep confirms the issue is resolved; old URLs returning 404 are normal missing resources. The client has no model-specific exclusion list: refreshing uses the current catalog, and historical references to removed models produce an actionable error. LoRA is outside the current Modellix capability and this package's scope.

## Actual production generation

Each row below completed against `https://api.modellix.ai` with a funded real key. Test task IDs and billing receipts are retained in the local acceptance evidence; no real task IDs or credentials are shipped in workflows.

| Path | Representative model | Observed native result |
| --- | --- | --- |
| Text-to-image | `google/nano-banana-2-lite` | 1024×1024 image in Preview Image |
| Image editing | `google/nano-banana-2-lite-edit` | Single-image edit, two-image native batch, and three ordered merged URL references |
| Text-to-video | `bytedance/seedance-2.0-mini-t2v` | 4 seconds, 480p; saved through native Save Video |
| First/last-frame I2V | `bytedance/seedance-2.0-mini-i2v` | Separate frame sockets; 4 seconds, 480p |
| Reference I2V | `bytedance/seedance-2.0-mini-i2v` | Explicit reference branch; 4 seconds, 480p |
| Video transformation | `alibaba/wan2.7-videoedit` | Native video upload, 2-second 720P output through Save Video |
| Speech generation | `minimax/speech-2.8-turbo` | WAV, 16 kHz, mono through Preview Audio |
| Speech transformation | `alibaba/cosyvoice-clone` | Generated synthetic reference voice, WAV output at 16 kHz |
| Submit only | `google/nano-banana-2-lite` | One fresh generation POST, zero queries/downloads; independent Snapshot/Wait recovery |
| Upload/URL helpers | Image, video and audio from the tasks above | Standalone uploads, aligned output lists, ordered merge, native/URL reference connections |

The ten recommended UI workflows were instantiated and serialized in the real frontend, saved and reopened, then executed through the real host using the matching already-paid attempt identities. All ten recovery runs produced zero additional generation or upload POSTs. This deliberately tests restoration and cache behavior without duplicating paid work. Fresh generation was verified separately in the representative paths above. An explicit new Submit-only attempt was also verified independently.

Actual `billing.status` / `billing.amount` receipts were read back; use the service receipt for charges rather than inferring cost from catalog price. The task endpoint's tested receipts do not include a currency field. No balance depletion, rate-limit stress test or quota modification was performed.

## Acceptance matrix

| Item | Evidence |
| --- | --- |
| AC-01 Installation and registration | Four real V3 nodes loaded; both an inspected directory archive and a fresh GitHub main clone cold-loaded all schemas, frontend assets and templates |
| AC-02 Credentials | Priority/clear/insecure-backend tests; actual Windows secure-store restart test; Settings session/clear/environment UI; workflow/history/settings/media/archives scanned for actual secrets |
| AC-03 Dynamic models | Live catalog, readable schema sweep, typed fields, independent nested combination choices, saved parameters and bindings |
| AC-04 T2I | Actual production Queue → Preview Image |
| AC-05 I2I | Actual single-image and two-image-batch edits; source conflict and scalar-list rejection checks |
| AC-06 I2V | Both live frame/reference branches; native Save Video |
| AC-07 Paid-request safety | Single-shot POST faults, concurrent attempts, unknown outcomes, failed/canceled tasks, malformed responses and protected attempt records |
| AC-08 Cancel | Actual host interruption flag exits a supervised local wait within two seconds; in-flight HTTP cancellation and unknown-submission protection. Codec threads may finish separately |
| AC-09 Recovery | Existing IDs and cross-restart attempts recovered with zero new generation/upload calls |
| AC-10 Examples and packaging | Ten UI/API examples, save/reopen and actual cached-host execution, one-source fixture checks, directory archive cold loading and secret scans |
| AC-11 Official documentation | Tutorial, sidebar and Product Updates maintained in the separate official docs repository; Mintlify validation, broken-link check and rendered local pages; successful Mintlify deployment and public tutorial read-back |
| AC-12 Execution modes | Fresh Submit only has one POST and no polling/download; Snapshot and Wait retrieve the same task; native output blockers tested per socket |
| AC-13 Native media and lists | Actual image/video/audio chains, two-image batch; real-host different-size multi-resource tensors, stereo sample-rate round trip and preserved video audio track |
| AC-14 Upload Media | Actual standalone image/video/audio upload workflows, content reuse and unknown-upload replay protection |
| AC-15 URL List | Ordered/duplicate/comma-preserving construction; actual merged three-reference generation; schema type/array constraints |
| AC-16 Cache and persistence | Atomic SQLite records, file locks, credential isolation, result checksums, protected live leases, expired results/abandoned-part cleanup, unknown/failed/canceled attempt protection |
| AC-17 Type/schema coverage | Seven live media categories; final 172/172 schema sweep and parser checks; removed-model/404 handling and text-output explanation |
| AC-18 Interaction | Model search/category/branch/source controls, conflict replacement, task recovery/new-attempt UI, key controls, save/reopen and 1200/1440/1920 viewport checks |

Local automated results at delivery: **79 pure Python tests** and **6 real ComfyUI host tests**. The host tests use actual ComfyUI types, codecs, execution/list machinery and connection validation, not global ComfyUI stubs. Controlled tests cover failures that should not be manufactured by spending real balance.

## Distribution status and limits

### Schema Review and Error Recovery — 2026-10-20

Five reported defects were reproduced with a real ComfyUI v0.40.0 frontend and a controlled local API using synthetic credentials and task IDs. An unaccepted schema change produced zero generation POSTs; explicit acceptance submitted once, and a repeat Queue added none. Reopening an old workflow retained the review gate. A 400 retained its cleaned `/prompt` guidance and did not replay; 404 errors identified the requested task or model. Get Task timeout/unknown-state errors and recovery used the current task ID. Clear Key removed all old catalog options, pricing, and fields; late-response, unchanged union-type, and partial-clear regressions passed. The form passed 1200/1440/1920 viewport checks. **131 pure Python tests, 7 frontend regression tests, and 7 real-host tests passed.** No real API key or paid call was used for these fault cases.

### Startup Compatibility — 2026-10-19

The runtime now enforces the declared minimum ComfyUI version before importing node implementations or registering routes. An actual ComfyUI v0.39.2 host started successfully but reported the minimum-version error and exposed none of the four Modellix nodes through `/object_info`. The real v0.40.0 loader registered all four nodes and all six local route definitions. Version-boundary tests also cover 0.40.0 pre-releases, newer development versions, and missing or invalid host metadata. **122 pure Python tests and 7 real-host tests passed.** This startup regression needs no API key or paid generation.

### Review Follow-Up — 2026-10-16

The maintenance review added regression coverage for schema changes retaining the same paid intent, structurally damaged protection records, preparation/lock deadlines, confirmed-ID error recovery, malformed task responses, custom result-directory links, and re-downloading an undecodable result. **108 pure Python tests and 6 real-host tests passed.**

All ten existing example workflows were recovered against production with zero additional generation or upload POSTs. One separate stable image intent exercised a fresh Submit-only POST, followed by Snapshot/Wait recovery and a real local result download; its receipt reported `billing.amount = 0.0306` without a currency field. Browser checks verified that a loading model selection cannot queue the previous model, New Attempt clears recovery state, credential refresh does not silently approve schema drift, and hidden serialization controls remain absent from the native canvas without losing prompt values. The workflow screenshots were captured after these fixes and checks, using the real host; credentials and task identifiers are concealed.

Git clone and versioned Registry installation are supported delivery channels. `python tools/package.py` builds the inspected `custom_nodes/modellix-comfyui` directory archive with root entry point, web extension and workflow resources. Python sdist/wheel metadata is also built and inspected; a wheel is not advertised as a ComfyUI install channel.

On **2026-10-23**, version **0.1.0** was published to [Comfy Registry](https://registry.comfy.org/publishers/modellix/nodes/modellix-comfyui) under publisher `modellix`. The downloaded CDN archive matched the inspected upload byte for byte. Both that archive and a separate ComfyUI-Manager **4.2.2** Registry installation cold-loaded four real V3 schemas, the frontend extension, ten templates, and five documentation images against the actual ComfyUI v0.40.0 host. The isolated Manager installation used already-satisfied host dependencies. Registry security review status is separate from this installation check; publication does not imply a verification badge. Public documentation deployment is checked separately after the official docs main push.

There is no remote-cancel/refund promise, automatic POST replay, remote file deletion, arbitrary authenticated API-origin setting, plaintext keyring fallback, LLM/text-output support, or local model-weight requirement. Native result files stay conservatively pinned for the host process lifetime. See the README for cache limits and configuration.
