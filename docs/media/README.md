# Media

The product tour the root [README.md](../../README.md) shows under *Product tour*.

| File | What it is |
| --- | --- |
| `product-tour.mp4` | The tour: English narration and subtitles, 4:29, 720p, about 9 MB. |
| `product-tour.jpg` | Its thumbnail. The README shows it and links it to the video. |

**Why a thumbnail and not a player.** GitHub removes `<video>` tags from
Markdown and opens a committed `.mp4` as a download, so a file in the repository
can only be linked. GitHub plays a video inline only when it was uploaded
through github.com, as a `https://github.com/user-attachments/assets/…` link.
To get that: edit the root README on github.com, drag `product-tour.mp4` onto
the comment in *Product tour*, and commit. The video is kept under 10 MB because
that is GitHub's upload limit for videos on a free plan.

Every screen was recorded from the demo build (`frontend/demo/`) over its sample
data. If the product changes enough that the tour no longer matches it,
re-record it rather than editing the video.
