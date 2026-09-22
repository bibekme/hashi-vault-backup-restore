import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

import boto3
import hvac
import typer
from botocore.exceptions import ClientError, NoCredentialsError
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()

VAULT_ADDR = os.environ["VAULT_ADDR"]
VAULT_TOKEN = os.environ["VAULT_TOKEN"]
S3_BUCKET = os.environ["SNAPSHOT_S3_BUCKET"]
S3_PREFIX = os.environ.get("SNAPSHOT_S3_PREFIX", "vault-backup")
BACKUP_DIR = Path(os.environ.get("SNAPSHOT_BACKUP_DIR", "/tmp/vault/backup"))
FILENAME_PREFIX = os.environ.get("SNAPSHOT_FILENAME_PREFIX", "vault-snapshot")


def get_vault_client() -> hvac.Client:
    client = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
    if not client.is_authenticated():
        console.print("[red]Vault authentication failed[/red]")
        raise typer.Exit(code=1)
    return client


@app.command()
def backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{FILENAME_PREFIX}-{timestamp}"
    local_path = BACKUP_DIR / filename

    client = get_vault_client()

    try:
        response = client.sys.take_raft_snapshot()
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)
    except hvac.exceptions.VaultError as e:
        console.print(f"[red]Snapshot failed:[/red] {e}")
        raise typer.Exit(code=1)

    try:
        s3 = boto3.client("s3")
        s3.upload_file(str(local_path), S3_BUCKET, f"{S3_PREFIX}/{filename}")
    except (ClientError, NoCredentialsError) as e:
        console.print(f"[red]Upload failed:[/red] {e}")
        console.print(f"Snapshot kept locally at {local_path}")
        raise typer.Exit(code=1)

    local_path.unlink()
    console.print(f"[green]Backup complete:[/green] s3://{S3_BUCKET}/{S3_PREFIX}/{filename}")


@app.command()
def list():
    try:
        s3 = boto3.client("s3")
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/")
    except (ClientError, NoCredentialsError) as e:
        console.print(f"[red]Could not list bucket:[/red] {e}")
        raise typer.Exit(code=1)

    objects = sorted(response.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)

    table = Table()
    table.add_column("Name")
    table.add_column("Size")
    table.add_column("Last Modified")

    for obj in objects:
        name = obj["Key"].removeprefix(f"{S3_PREFIX}/")
        table.add_row(name, f"{obj['Size']} bytes", str(obj["LastModified"]))

    console.print(table)


@app.command()
def cleanup(days: int = typer.Option(7, "--days")):
    s3 = boto3.client("s3")

    try:
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/")
    except (ClientError, NoCredentialsError) as e:
        console.print(f"[red]Could not list bucket:[/red] {e}")
        raise typer.Exit(code=1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    objects = response.get("Contents", [])
    to_delete = [obj for obj in objects if obj["LastModified"] < cutoff]

    if not to_delete:
        console.print("Nothing to clean up")
        return

    for obj in to_delete:
        s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
        console.print(f"Deleted {obj['Key']}")

    console.print(f"[green]Removed {len(to_delete)} backup(s) older than {days} days[/green]")

@app.command()
def force_restore(name: str = typer.Option(..., "--name")):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    local_path = BACKUP_DIR / name

    try:
        s3 = boto3.client("s3")
        s3.download_file(S3_BUCKET, f"{S3_PREFIX}/{name}", str(local_path))
    except ClientError as e:
        console.print(f"[red]Backup not found or download failed:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"[yellow]Force restoring from {name} — this bypasses seal validation[/yellow]")

    try:
        with open(local_path, "rb") as f:
            response = requests.post(
                f"{VAULT_ADDR}/v1/sys/storage/raft/snapshot-force",
                headers={"X-Vault-Token": VAULT_TOKEN},
                data=f,
                timeout=300,
            )
        response.raise_for_status()
    except requests.RequestException as e:
        console.print(f"[red]Force restore failed:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"[green]Force restore complete from {name}[/green]")

@app.command()
def restore(name: str = typer.Option(..., "--name")):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    local_path = BACKUP_DIR / name

    try:
        s3 = boto3.client("s3")
        s3.download_file(S3_BUCKET, f"{S3_PREFIX}/{name}", str(local_path))
    except ClientError as e:
        console.print(f"[red]Backup not found or download failed:[/red] {e}")
        raise typer.Exit(code=1)

    client = get_vault_client()

    try:
        with open(local_path, "rb") as f:
            client.sys.restore_raft_snapshot(f)
            client.sys.restore_raft_snapshot(f)
    except hvac.exceptions.VaultError as e:
        console.print(f"[red]Restore failed:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"[green]Restore complete from {name}[/green]")


if __name__ == "__main__":
    app()

