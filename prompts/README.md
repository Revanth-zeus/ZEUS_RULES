# Prompts

Reusable prompts for the documentary video pipeline.

| File | Use |
| --- | --- |
| [vox-style-master-prompt.md](vox-style-master-prompt.md) | Full state-machine prompt: niche, ideas, Fern-style script, ElevenLabs voiceover, beat breakdown, collage image prompts (.txt), universal video prompt, thumbnails. |
| [google-flow-multi-image-prompt.md](google-flow-multi-image-prompt.md) | Google Flow instruction to generate every image from the .txt feed, then animate each one with the 6-second universal video prompt it contains. |

Typical flow: run the master prompt through STATE 7 to get `[topic-slug]-prompts.txt`, paste it into Google Flow with the multi-image prompt, then hand it the 6-second universal video prompt (in that file) when it asks. The 10-second STATE 8 version is for tools that render 10-second clips.
