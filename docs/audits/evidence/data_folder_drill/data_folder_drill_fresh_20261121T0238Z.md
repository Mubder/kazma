# Data-folder drill (fresh): passed

Commit 090e2d0f03, finished 2026-11-21 02:42 UTC. Evidence: `data_folder_drill_fresh_20261121T0238Z.json`.

| Check | |
|---|---|
| install exit 0 | yes |
| service runs from the folder | yes |
| it runs there | yes |
| the data is kept | yes |
| a new segment is archived there | yes |

Kazma's container engine was not drilled here (it needs WSL and a restart on the VM);
`tests/test_data_folder.py` holds its move on a fake machine.
