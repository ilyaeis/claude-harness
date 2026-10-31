---
name: rclone
description: Use rclone to upload files to, or download files from, the user's Google Drive. Trigger when the user asks to upload/download/copy/move a file or folder to or from Google Drive, Drive, or "my drive".
---

# rclone: Google Drive upload/download

rclone is installed at `~/.local/bin/rclone`. The user's Google Drive is configured as the remote `gdrive:`.

## Before transferring

1. Confirm the remote is authenticated:
   ```bash
   timeout 15 rclone lsd gdrive:
   ```
   If it errors with "empty token found - please run rclone config reconnect", tell the user to run `rclone config reconnect gdrive:` themselves in their own terminal (it needs an interactive browser login — it cannot be run through a one-shot shell command). Wait for them to confirm before continuing.

2. If you don't know the exact destination/source folder name, list it first rather than guessing:
   ```bash
   rclone lsd gdrive:                # top-level folders
   rclone lsd "gdrive:path/to/dir"   # subfolders
   rclone ls  "gdrive:path/to/dir"   # files in a folder
   ```

## Upload (local → Drive)

```bash
rclone copy /local/path/to/file "gdrive:Target Folder/"
rclone copy /local/path/to/dir  "gdrive:Target Folder/"   # copies dir contents into Target Folder
```

`copy` only adds/updates files — it never deletes anything, so it's safe to run without asking.

## Download (Drive → local)

```bash
rclone copy "gdrive:Some Folder/file.ext" /local/destination/
rclone copy "gdrive:Some Folder" /local/destination/
```

Same safety note: `copy` doesn't delete local files, safe by default.

## Quoting

Always quote the `gdrive:...` argument when the path contains spaces (Drive folder names often do).

## Do not use sync/delete/move without asking

`rclone sync`, `rclone move`, `rclone delete`, `rclone purge`, or any `--delete-*` flag can permanently remove files (locally or in Drive). Only use these if the user explicitly asks for that behavior, and confirm first — run with `--dry-run` and show what would change before doing it for real.

## Progress on large transfers

Add `-P` (`--progress`) for a live progress bar on big files/folders.
