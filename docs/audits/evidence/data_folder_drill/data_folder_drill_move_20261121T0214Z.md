# Data-folder drill (move): passed

Commit 46365db64c, finished 2026-11-21 02:30 UTC. Evidence: `data_folder_drill_move_20261121T0214Z.json`.

| Check | |
|---|---|
| move exit 0 | yes |
| service runs from the folder | yes |
| it runs there | yes |
| the data came along | yes |
| a new segment is archived there | yes |
| postgresql was removed | yes |
| reinstall exit 0 | yes |
| the new service runs the kept database | yes |
| the data is still there | yes |
| it archives there again | yes |

Kazma's container engine was not drilled here (it needs WSL and a restart on the VM);
`tests/test_data_folder.py` holds its move on a fake machine.
