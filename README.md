# vault-snapshot

Python CLI helper for Vault/OpenBao Raft snapshot backup, restore, and cleanup to S3.

## Install Pip dependencies

```
pip install -r requirements.txt
```

## Config

Set these before running:

```
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=your-vault-token
export SNAPSHOT_S3_BUCKET=your-bucket
```

Optional, with defaults:

```
export SNAPSHOT_S3_PREFIX=vault-backup
export SNAPSHOT_BACKUP_DIR=/tmp/vault/backup
export SNAPSHOT_FILENAME_PREFIX=vault-snapshot
```

AWS credentials are picked up the normal boto3 way (env vars, `~/.aws/credentials`, or instance role).

## Usage

Take a snapshot and upload it to S3:

```
python app.py backup
```

List snapshots, newest first:

```
python app.py list
```

Restore a snapshot by name (from the `list` output):

```
python app.py restore --name vault-snapshot-20260923T041007Z
```

Delete snapshots older than N days (default 7):

```
python app.py cleanup --days 7
```

Delete everything, including today's:

```
python app.py cleanup --days 0
```

## Notes

- Snapshot filenames use UTC timestamps so they sort correctly and don't depend on server timezone.
- `restore` overwrites the current Raft data, there's no undo.
- No locking, this is meant to be run manually or from a single scheduled job, not in parallel.
