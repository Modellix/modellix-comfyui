# Workflow examples

Import the UI JSON files from [workflows](workflows) by dragging a file onto ComfyUI, or use the package's Template Browser entry. Matching API prompts live in [api](api). Both formats use the same node schemas and version-1 parameter/binding format. They contain no API key, real task ID, or external demo media URL.

Configure Settings → Modellix first. Every recommended example creates its own source media and then connects native outputs, so no local model weights or manual input URL are required. These examples **incur charges** when queued with a new attempt. Source and transformation nodes are output nodes; mute unused nodes. Repeat Queue uses saved attempt identities; use New Attempt when you intend another paid request.

| Example | What to learn |
| --- | --- |
| text-to-image | Single Generate → native Preview Image |
| image-edit | Native image result → automatic image-array upload → edit |
| text-video | Text-to-video → native Save Video |
| first-last-video | Separate first/last IMAGE bindings; source image automatically uploads once |
| reference-video | Explicit reference branch with ordered image-array input |
| video-edit | Native VIDEO output → automatic upload → video transformation |
| speech | Speech generation → native Preview Audio |
| speech-transform | Generated synthetic reference AUDIO → automatic upload → speech transformation |
| upload-url-list | Independent Upload Media → URL List → array-valued reference field |
| submit-and-get-task | Submit only → task ID connection → Get Task Wait → Preview Image |

The image-to-video examples generate the source image once and reuse it. The first/last-frame example intentionally uses the same illustration at both ends; replace either connection to animate between distinct images. For multi-image editing, insert a native image-batch node with same-sized inputs and connect its IMAGE output to the image array. Scalar image fields reject a batch containing more than one image.

For native file inputs, replace a source Generate node with Load Image, Load Video or Load Audio and bind the corresponding schema field. Video/audio duration, resolution and content limits depend on the selected model. Use your own media or generated media you are authorized to transform. Speech transformation here uses synthetic narration created in the same workflow.

API clients submit the matching `api/*.json` object as the `prompt` property to ComfyUI's `/prompt` route. The host must already have a configured key. Preserve `node_instance_id` and `run_nonce` across retries; change `run_nonce` explicitly for a new paid attempt. For recovery, use Get Task with your own task ID; do not replace an unknown submission with a fresh generation automatically.

UI files and `example_workflows/` copies are serialized from these API examples through a real ComfyUI frontend. Regenerate the API source with `python tools/examples.py`; do not hand-maintain two divergent workflow formats. All supplied models are checked against the live catalog at execution time. Review the [support matrix](support-matrix.md) for actual validation and known external exclusions.
