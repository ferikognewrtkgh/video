from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request


OWNER = "ferikognewrtkgh"
REPOSITORY = "video"
BRANCH = "main"
LOCAL_COMMIT = "bb91ae3"


def git(*arguments: str, text: bool = True):
    output = subprocess.check_output(["git", *arguments])
    return output.decode("utf-8").strip() if text else output


def github_token() -> str:
    raw = subprocess.check_output(
        ["git", "credential", "fill"],
        input=b"protocol=https\nhost=github.com\n\n",
    )
    values: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    token = values.get("password")
    if not token:
        raise RuntimeError("Git Credential Manager did not return a GitHub credential")
    return token


def api(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MangaFlow-Codex-Push",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {path} failed with {error.code}: {detail}") from error


def identity(commit: str, prefix: str) -> dict[str, str]:
    placeholders = {
        "author": ("%an", "%ae", "%aI"),
        "committer": ("%cn", "%ce", "%cI"),
    }
    name, email, date = (git("show", "-s", f"--format={item}", commit) for item in placeholders[prefix])
    return {"name": name, "email": email, "date": date}


def main() -> None:
    token = github_token()
    local_commit = git("rev-parse", LOCAL_COMMIT)
    parent_commit = git("rev-parse", f"{local_commit}^")
    local_tree = git("rev-parse", f"{local_commit}^{{tree}}")
    parent_tree = git("rev-parse", f"{parent_commit}^{{tree}}")
    ref_path = f"/repos/{OWNER}/{REPOSITORY}/git/ref/heads/{BRANCH}"
    remote_commit = api(token, "GET", ref_path)["object"]["sha"]
    if remote_commit == local_commit:
        print(f"Remote {BRANCH} already points to {local_commit}")
        return
    if remote_commit != parent_commit:
        raise RuntimeError(
            f"Remote {BRANCH} moved from expected parent {parent_commit} to {remote_commit}; refusing non-fast-forward update"
        )

    entries: list[dict[str, str | None]] = []
    changes = git("diff-tree", "--no-commit-id", "--name-status", "-r", local_commit).splitlines()
    for index, line in enumerate(changes, 1):
        status, path = line.split("\t", 1)
        if status.startswith("D"):
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        tree_line = git("ls-tree", local_commit, "--", path)
        metadata, _ = tree_line.split("\t", 1)
        mode, object_type, local_blob = metadata.split()
        content = git("cat-file", "blob", local_blob, text=False)
        result = api(token, "POST", f"/repos/{OWNER}/{REPOSITORY}/git/blobs", {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        if result["sha"] != local_blob:
            raise RuntimeError(f"Blob SHA mismatch for {path}: local={local_blob} remote={result['sha']}")
        entries.append({"path": path, "mode": mode, "type": object_type, "sha": local_blob})
        print(f"Uploaded blob {index}/{len(changes)}: {path}")

    created_tree = api(token, "POST", f"/repos/{OWNER}/{REPOSITORY}/git/trees", {
        "base_tree": parent_tree,
        "tree": entries,
    })["sha"]
    if created_tree != local_tree:
        raise RuntimeError(f"Tree SHA mismatch: local={local_tree} remote={created_tree}")
    print(f"Verified tree: {local_tree}")

    message = git("log", "-1", "--format=%B", local_commit)
    created_commit = api(token, "POST", f"/repos/{OWNER}/{REPOSITORY}/git/commits", {
        "message": message,
        "tree": local_tree,
        "parents": [parent_commit],
        "author": identity(local_commit, "author"),
        "committer": identity(local_commit, "committer"),
    })["sha"]
    if created_commit != local_commit:
        raise RuntimeError(f"Commit SHA mismatch: local={local_commit} remote={created_commit}; branch was not updated")
    print(f"Verified commit: {local_commit}")

    api(token, "PATCH", f"/repos/{OWNER}/{REPOSITORY}/git/refs/heads/{BRANCH}", {
        "sha": local_commit,
        "force": False,
    })
    verified = api(token, "GET", ref_path)["object"]["sha"]
    if verified != local_commit:
        raise RuntimeError(f"Remote verification failed: expected={local_commit} actual={verified}")
    print(f"Updated {OWNER}/{REPOSITORY}:{BRANCH} to {verified}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
