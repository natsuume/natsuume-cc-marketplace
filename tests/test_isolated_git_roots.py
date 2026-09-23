"""隔離ルート (env CLAUDE_ISOLATED_GIT_ROOTS) 配下の repo への commit 免除の契約テスト。

git-guardrails の commit hook (block-default-branch-commit.sh) と auto-lint-check の
block-commit-lint.sh に PreToolUse JSON を stdin で渡し、コマンド全体が免除テンプレート
(`git -C <ABS> commit ...` / `git -C <ABS> add ... && git -C <ABS> commit ...`) に
一致し、対象が許可ルート配下の repo である commit だけが免除され、それ以外は従来どおり
deny されることを検査する。判定器 (isolated-commit-template.sh) を bash で直接呼ぶ
単体テストも含む。

hook の cwd には許可ルート外に作る「実 repo 相当」(master 上) を使い、テストを実行する
worktree 自身の git 状態に依存しない。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GIT_GUARDRAILS_HOOK_DIR = ROOT / "plugins" / "git-guardrails" / "hooks" / "scripts"
GG_COMMIT_HOOK = GIT_GUARDRAILS_HOOK_DIR / "block-default-branch-commit.sh"
GG_PUSH_HOOK = GIT_GUARDRAILS_HOOK_DIR / "block-default-branch-push.sh"
AL_COMMIT_HOOK = (
    ROOT / "plugins" / "auto-lint-check" / "hooks" / "scripts" / "block-commit-lint.sh"
)
GG_TEMPLATE_LIB = GIT_GUARDRAILS_HOOK_DIR / "lib" / "isolated-commit-template.sh"

ISOLATED_ROOTS_ENV = "CLAUDE_ISOLATED_GIT_ROOTS"

# hook / fixture の git 解決をテスト側の環境から切り離すため、継承しない env。
INHERITED_GIT_ENV_TO_DROP = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_CEILING_DIRECTORIES",
)


@unittest.skipUnless(
    shutil.which("bash")
    and shutil.which("git")
    and shutil.which("jq")
    and shutil.which("python3"),
    "hook integration requires bash, git, jq, and python3",
)
class IsolatedGitRootsTestBase(unittest.TestCase):
    """隔離ルート・隔離 repo・実 repo 相当の fixture と hook 起動を提供する。

    fixture のレイアウト (すべて一時ディレクトリ ``base`` 配下):

    - ``real``: 許可ルート外の「実 repo 相当」。master 上。hook の cwd に使う
    - ``roots/allowed``: 許可ルート
    - ``roots/allowed/iso``: 許可ルート配下の隔離 repo。master 上
    - ``roots/allowedc/iso``: 許可ルートと接頭辞だけ一致するルート外の repo
    - ``outside/repo``: 許可ルート外の repo
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

        empty_git_config = self.base / "gitconfig"
        empty_git_config.write_text("", encoding="utf-8")
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key not in INHERITED_GIT_ENV_TO_DROP and key != ISOLATED_ROOTS_ENV
        }
        self.env["GIT_CONFIG_GLOBAL"] = str(empty_git_config)
        self.env["GIT_CONFIG_SYSTEM"] = str(empty_git_config)

        self.real_repo = self.base / "real"
        self.init_repo(self.real_repo)

        self.allowed_root = self.base / "roots" / "allowed"
        self.allowed_root.mkdir(parents=True)
        self.iso_repo = self.allowed_root / "iso"
        self.init_repo(self.iso_repo)

        self.outside_repo = self.base / "outside" / "repo"
        self.init_repo(self.outside_repo)

    # -- fixture ------------------------------------------------------------

    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            capture_output=True,
            env=self.env,
        )
        return result.stdout.strip()

    def init_repo(self, repo: Path) -> None:
        """master 上に 1 commit を持つ repo を作る (user.name / user.email はローカル設定)。"""
        repo.mkdir(parents=True)
        self.git(repo, "init")
        self.git(repo, "symbolic-ref", "HEAD", "refs/heads/master")
        self.git(repo, "config", "user.name", "Marketplace Test")
        self.git(repo, "config", "user.email", "marketplace@example.invalid")
        self.git(repo, "config", "commit.gpgsign", "false")
        (repo / "file.txt").write_text("base\n", encoding="utf-8")
        self.git(repo, "add", "file.txt")
        self.git(repo, "commit", "-m", "base")
        self.assertEqual("master", self.git(repo, "symbolic-ref", "--short", "HEAD"))

    # -- hook 実行 ----------------------------------------------------------

    def run_hook(
        self,
        hook: Path,
        command: str,
        *,
        roots: str | None,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        hook_cwd = self.real_repo if cwd is None else cwd
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-isolated-git-roots",
            "cwd": str(hook_cwd),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        env = dict(self.env)
        if roots is not None:
            env[ISOLATED_ROOTS_ENV] = roots
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(hook)],
            cwd=str(hook_cwd),
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=120,
        )

    # -- assertions ---------------------------------------------------------

    def assert_allowed(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(
            result.stdout,
            b"",
            f"allow を期待したが hook が出力した: {result.stdout.decode()}",
        )

    def deny_reason(self, result: subprocess.CompletedProcess[bytes]) -> str:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(result.stdout, b"", "deny を期待したが hook が allow した")
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        return output["permissionDecisionReason"]

    def assert_denied(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.deny_reason(result)


class GitGuardrailsCommitHookIsolatedRootsTest(IsolatedGitRootsTestBase):
    """git-guardrails block-default-branch-commit.sh の免除契約。"""

    # -- allow ----------------------------------------------------------------

    def test_git_c_commit_into_isolated_repo_is_allowed(self) -> None:
        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {self.iso_repo} commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_allowed(result)

    def test_cd_then_commit_into_isolated_repo_is_denied(self) -> None:
        # `cd <dir> && git commit` は免除テンプレートに含まれない。
        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"cd {self.iso_repo} && git commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_denied(result)

    def test_plain_commit_with_hook_cwd_in_isolated_repo_on_master_is_denied(
        self,
    ) -> None:
        # 免除テンプレートは `git -C <ABS>` を必須とするため、hook cwd が隔離 repo
        # (master 上) でも素の `git commit` は従来どおり default branch 上 commit として deny。
        result = self.run_hook(
            GG_COMMIT_HOOK,
            "git commit -m x",
            roots=str(self.allowed_root),
            cwd=self.iso_repo,
        )

        self.assert_denied(result)

    def test_root_given_as_symlink_allows_repo_under_its_target(self) -> None:
        root_link = self.base / "root-link"
        root_link.symlink_to(self.allowed_root, target_is_directory=True)

        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {self.iso_repo.resolve()} commit -m x",
            roots=str(root_link),
        )

        self.assert_allowed(result)

    def test_invalid_entries_are_ignored_and_valid_entry_is_used(self) -> None:
        roots = ":".join(
            [
                "",
                "relative/root",
                str(self.base / "does-not-exist"),
                "",
                str(self.allowed_root),
                "",
            ]
        )

        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {self.iso_repo} commit -m x",
            roots=roots,
        )

        self.assert_allowed(result)

    # -- deny -----------------------------------------------------------------

    def test_without_env_git_c_commit_remains_target_mismatch_denied(self) -> None:
        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {self.iso_repo} commit -m x",
            roots=None,
        )

        self.assertIn("git -C", self.deny_reason(result))

    def test_without_env_cd_commit_remains_target_mismatch_denied(self) -> None:
        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"cd {self.iso_repo} && git commit -m x",
            roots=None,
        )

        self.assertIn("git -C", self.deny_reason(result))

    def test_empty_env_git_c_commit_remains_denied(self) -> None:
        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {self.iso_repo} commit -m x",
            roots="",
        )

        self.assert_denied(result)

    def test_target_outside_allowed_root_is_denied(self) -> None:
        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {self.outside_repo} commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_denied(result)

    def test_linked_worktree_of_outside_repo_under_root_is_denied(self) -> None:
        # ルート外 repo の master を、許可ルート配下の linked worktree として checkout する。
        self.git(self.outside_repo, "switch", "-c", "other")
        worktree = self.allowed_root / "wt"
        self.git(self.outside_repo, "worktree", "add", str(worktree), "master")

        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {worktree} commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_denied(result)

    def test_symlink_under_root_pointing_outside_is_denied(self) -> None:
        link = self.allowed_root / "link"
        link.symlink_to(self.outside_repo, target_is_directory=True)

        for command in (
            f"git -C {link} commit -m x",
            f"cd {link} && git commit -m x",
        ):
            with self.subTest(command=command):
                result = self.run_hook(
                    GG_COMMIT_HOOK, command, roots=str(self.allowed_root)
                )

                self.assert_denied(result)

    def test_path_boundary_prefix_match_is_denied(self) -> None:
        sibling_repo = self.base / "roots" / "allowedc" / "iso"
        self.init_repo(sibling_repo)

        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {sibling_repo} commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_denied(result)

    def test_statically_unresolvable_forms_are_denied(self) -> None:
        # 実行時には隔離 repo を指す形でも、静的に解決できなければ免除しない。
        extra_env = {
            "ISO": str(self.iso_repo),
            "HOME": str(self.allowed_root),
        }
        iso = self.iso_repo
        commands = (
            'cd "$ISO" && git commit -m x',
            "cd $ISO && git commit -m x",
            'git -C "$ISO" commit -m x',
            "cd ~/iso && git commit -m x",
            f"cd {iso} && cd - && git commit -m x",
            f"pushd {iso} && git commit -m x",
            f"cd {iso} && popd && git commit -m x",
            f"(cd {iso} && git commit -m x)",
            f"{{ cd {iso} && git commit -m x; }}",
            f"GIT_DIR={iso}/.git git commit -m x",
            f"git --git-dir={iso}/.git commit -m x",
            f"git --work-tree={iso} commit -m x",
            f"export GIT_DIR={iso}/.git && git commit -m x",
        )
        for command in commands:
            with self.subTest(command=command):
                result = self.run_hook(
                    GG_COMMIT_HOOK,
                    command,
                    roots=str(self.allowed_root),
                    extra_env=extra_env,
                )

                self.assert_denied(result)

    def test_mixed_isolated_and_non_isolated_commits_are_denied(self) -> None:
        # 2 つ目の commit は hook cwd (実 repo 相当、master 上) を対象とする。
        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {self.iso_repo} commit -m a && git commit -m b",
            roots=str(self.allowed_root),
        )

        self.assert_denied(result)


class GitGuardrailsPushHookIsolatedRootsTest(IsolatedGitRootsTestBase):
    """免除は commit hook に限定され、push hook は従来どおり deny する。"""

    def test_git_c_push_from_isolated_repo_remains_denied(self) -> None:
        result = self.run_hook(
            GG_PUSH_HOOK,
            f"git -C {self.iso_repo} push",
            roots=str(self.allowed_root),
        )

        self.assert_denied(result)


class AutoLintBlockCommitLintIsolatedRootsTest(IsolatedGitRootsTestBase):
    """auto-lint-check block-commit-lint.sh の repo override deny の免除契約。"""

    def assert_repo_override_denied(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> None:
        self.assertIn("repo override", self.deny_reason(result))

    def test_git_c_commit_into_isolated_repo_is_allowed(self) -> None:
        result = self.run_hook(
            AL_COMMIT_HOOK,
            f"git -C {self.iso_repo} commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_allowed(result)

    def test_without_env_git_c_commit_remains_repo_override_denied(self) -> None:
        result = self.run_hook(
            AL_COMMIT_HOOK,
            f"git -C {self.iso_repo} commit -m x",
            roots=None,
        )

        self.assert_repo_override_denied(result)

    def test_target_outside_allowed_root_remains_repo_override_denied(self) -> None:
        result = self.run_hook(
            AL_COMMIT_HOOK,
            f"git -C {self.outside_repo} commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_repo_override_denied(result)


class PrecedingSegmentsIsolatedRootsTest(IsolatedGitRootsTestBase):
    """commit の前段に置けるのは、同じ対象への `git -C <ABS> add` (テンプレート T2) だけ。

    判定は hook 実行時点のファイルシステム状態で行うため、判定後・commit 前に対象 dir
    や repo レイアウトを差し替えうる前段コマンドがあれば免除しない (GG / AL 共通)。
    """

    HOOKS = (GG_COMMIT_HOOK, AL_COMMIT_HOOK)

    def assert_denied_by_both_hooks(self, command: str) -> None:
        for hook in self.HOOKS:
            with self.subTest(hook=hook.name):
                result = self.run_hook(hook, command, roots=str(self.allowed_root))

                reason = self.deny_reason(result)
                if hook == AL_COMMIT_HOOK:
                    self.assertIn("repo override", reason)

    def test_replacing_target_with_symlink_before_commit_is_denied(self) -> None:
        iso = self.iso_repo
        self.assert_denied_by_both_hooks(
            f"rm -rf {iso} && ln -s {self.real_repo} {iso} && git -C {iso} commit -m x"
        )

    def test_moving_target_before_commit_is_denied(self) -> None:
        iso = self.iso_repo
        self.assert_denied_by_both_hooks(
            f"mv {iso} {self.allowed_root / 'moved'} && git -C {iso} commit -m x"
        )

    def test_harmless_looking_preceding_command_is_denied(self) -> None:
        iso = self.iso_repo
        self.assert_denied_by_both_hooks(f"touch {iso}/f && git -C {iso} commit -m x")

    def test_repo_layout_change_before_commit_is_denied(self) -> None:
        iso = self.iso_repo
        self.assert_denied_by_both_hooks(
            f"git -C {iso} config core.worktree {self.real_repo} "
            f"&& git -C {iso} commit -m x"
        )

    def test_git_add_in_isolated_repo_before_commit_is_allowed(self) -> None:
        iso = self.iso_repo
        for hook in self.HOOKS:
            with self.subTest(hook=hook.name):
                result = self.run_hook(
                    hook,
                    f"git -C {iso} add f && git -C {iso} commit -m x",
                    roots=str(self.allowed_root),
                )

                self.assert_allowed(result)


class ExecutionTimeTargetDivergenceIsolatedRootsTest(IsolatedGitRootsTestBase):
    """判定したパスと実行時の対象が食い違いうる形は免除しない (GG / AL 共通)。

    - redirection の fd 番号とパス末尾の数字の混同
    - `;` 区切りで cd が失敗したときに commit が元の cwd で実行される経路
    - git dir 内部の symlink によるルート外 repo への脱出

    redirection・`;`・`cd` はいずれも免除テンプレートに含まれないため deny になる。
    """

    HOOKS = (GG_COMMIT_HOOK, AL_COMMIT_HOOK)

    def assert_denied_by_both_hooks(self, command: str) -> None:
        for hook in self.HOOKS:
            with self.subTest(hook=hook.name, command=command):
                result = self.run_hook(hook, command, roots=str(self.allowed_root))

                reason = self.deny_reason(result)
                if hook == AL_COMMIT_HOOK:
                    self.assertIn("repo override", reason)

    def test_digit_suffixed_symlink_to_outside_repo_is_denied(self) -> None:
        # `<root>/iso` は隔離 repo、`<root>/iso2` はルート外 repo への symlink。
        iso2 = self.allowed_root / "iso2"
        iso2.symlink_to(self.outside_repo, target_is_directory=True)

        for command in (
            f"cd {iso2} && git commit -m x",
            f"git -C {iso2} commit -m x",
            f"cd {iso2}>&1 && git commit -m x",
        ):
            self.assert_denied_by_both_hooks(command)

    def test_digit_suffixed_git_c_path_followed_by_redirection_is_denied(self) -> None:
        # bash は `<root>/iso2>&1` をパス `<root>/iso2` と redirection `>&1` に分ける。
        # auto-lint-check の parser はこの形を commit invocation として検出しないため、
        # 免除判定の対象になる git-guardrails だけを検査する。
        iso2 = self.allowed_root / "iso2"
        iso2.symlink_to(self.outside_repo, target_is_directory=True)

        result = self.run_hook(
            GG_COMMIT_HOOK,
            f"git -C {iso2}>&1 commit -m x",
            roots=str(self.allowed_root),
        )

        self.assert_denied(result)

    def test_redirection_on_commit_invocation_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x > /dev/null"
        )

    def test_semicolon_separated_cd_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(f"cd {self.iso_repo} ; git commit -m x")

    def test_refs_symlinked_to_outside_repo_is_denied(self) -> None:
        refs = self.iso_repo / ".git" / "refs"
        shutil.rmtree(refs)
        refs.symlink_to(self.outside_repo / ".git" / "refs", target_is_directory=True)

        self.assert_denied_by_both_hooks(f"git -C {self.iso_repo} commit -m x")

    def test_index_symlinked_to_outside_repo_in_non_worktree_repo_is_denied(self) -> None:
        index = self.iso_repo / ".git" / "index"
        if index.exists() or index.is_symlink():
            index.unlink()
        index.symlink_to(self.outside_repo / ".git" / "index")

        self.assert_denied_by_both_hooks(f"git -C {self.iso_repo} commit -m x")

    def test_any_symlink_directly_under_git_dir_is_denied(self) -> None:
        editmsg = self.iso_repo / ".git" / "COMMIT_EDITMSG"
        if editmsg.exists() or editmsg.is_symlink():
            editmsg.unlink()
        editmsg.symlink_to(self.outside_repo / "COMMIT_EDITMSG_TARGET")

        self.assert_denied_by_both_hooks(f"git -C {self.iso_repo} commit -m x")


class CommitRecognitionMismatchIsolatedRootsTest(IsolatedGitRootsTestBase):
    """hook と免除判定の commit 認識が食い違いうる形は免除しない (GG / AL 共通)。

    hook cwd は master 上の実 repo 相当。redirection はコマンド内のどの位置にあっても
    免除テンプレートに一致しない。
    """

    HOOKS = (GG_COMMIT_HOOK, AL_COMMIT_HOOK)

    def assert_denied_by_both_hooks(self, command: str) -> None:
        for hook in self.HOOKS:
            with self.subTest(hook=hook.name, command=command):
                result = self.run_hook(hook, command, roots=str(self.allowed_root))

                reason = self.deny_reason(result)
                if hook == AL_COMMIT_HOOK:
                    self.assertIn("repo override", reason)

    def test_later_commit_preceded_by_stdout_redirection_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x && >/dev/null git commit -m y"
        )

    def test_later_commit_preceded_by_stderr_redirection_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x && 2>/dev/null git commit -m y"
        )

    def test_redirection_after_last_commit_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x && git log 2>&1"
        )

    def test_exemption_requires_exactly_one_commit_invocation(self) -> None:
        # 免除判定器は commit invocation がちょうど 1 つのテンプレートだけを免除する。
        # commit が無い・2 つ以上あるコマンドは免除しない。
        script = f'source "{GG_TEMPLATE_LIB}" && isolated_commit_template_exempts "$1"'
        env = dict(self.env)
        env[ISOLATED_ROOTS_ENV] = str(self.allowed_root)
        iso = self.iso_repo

        def exemption_status(command: str) -> int:
            return subprocess.run(
                ["bash", "-c", script, "bash", command],
                cwd=str(self.real_repo),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=60,
            ).returncode

        self.assertEqual(0, exemption_status(f"git -C {iso} commit -m x"))
        for command in (
            f"git -C {iso} add f",
            f"git -C {iso} commit -m a && git -C {iso} commit -m b",
        ):
            with self.subTest(command=command):
                self.assertEqual(1, exemption_status(command))


class SingleLineAndNestedRefIsolatedRootsTest(IsolatedGitRootsTestBase):
    """改行を含むコマンドと、branch ref 格納先の入れ子の symlink は免除しない (GG / AL 共通)。

    対照として、許可ルート配下の repo の linked worktree (通常の branch) は免除する。
    hook cwd は master 上の実 repo 相当。
    """

    HOOKS = (GG_COMMIT_HOOK, AL_COMMIT_HOOK)

    def assert_denied_by_both_hooks(self, command: str) -> None:
        for hook in self.HOOKS:
            with self.subTest(hook=hook.name, command=command):
                result = self.run_hook(hook, command, roots=str(self.allowed_root))

                reason = self.deny_reason(result)
                if hook == AL_COMMIT_HOOK:
                    self.assertIn("repo override", reason)

    def test_escaped_backslash_before_newline_does_not_hide_later_commit(self) -> None:
        # 行末の `\\` は escape された backslash で行継続ではない。bash は改行の後ろを
        # 別コマンドとして hook cwd の repo で実行する。
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x \\\\\ngit commit -m y"
        )

    def test_newline_inside_quoted_message_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(f'git -C {self.iso_repo} commit -m "a\nb"')

    def test_refs_heads_symlinked_to_outside_repo_is_denied(self) -> None:
        heads = self.iso_repo / ".git" / "refs" / "heads"
        shutil.rmtree(heads)
        heads.symlink_to(
            self.outside_repo / ".git" / "refs" / "heads", target_is_directory=True
        )

        self.assert_denied_by_both_hooks(f"git -C {self.iso_repo} commit -m x")

    def test_worktree_specific_ref_symlinked_to_outside_repo_is_denied(self) -> None:
        # linked worktree の HEAD が worktree 固有の ref (refs/worktree/*) を指す場合、ref は
        # common dir ではなく worktree の git dir 配下に格納される。その入れ子の symlink 経由で
        # ルート外 repo の refs/heads を更新する経路は免除しない。
        worktree = self.allowed_root / "wt"
        self.git(self.iso_repo, "worktree", "add", str(worktree), "-b", "side")
        self.git(worktree, "symbolic-ref", "HEAD", "refs/worktree/x")
        worktree_git_dir = Path(self.git(worktree, "rev-parse", "--absolute-git-dir"))
        (worktree_git_dir / "refs").mkdir(exist_ok=True)
        (worktree_git_dir / "refs" / "worktree").symlink_to(
            self.outside_repo / ".git" / "refs" / "heads", target_is_directory=True
        )

        self.assert_denied_by_both_hooks(f"git -C {worktree} commit -m x")

    def test_linked_worktree_of_repo_under_root_is_allowed(self) -> None:
        worktree = self.allowed_root / "wt"
        self.git(self.iso_repo, "worktree", "add", str(worktree), "-b", "side")

        for hook in self.HOOKS:
            with self.subTest(hook=hook.name):
                result = self.run_hook(
                    hook, f"git -C {worktree} commit -m x", roots=str(self.allowed_root)
                )

                self.assert_allowed(result)


# 免除テンプレートに一致する入力 ({I} は対象 dir、{F} は -F に渡す絶対パスに置換する)。
TEMPLATE_ALLOW_CASES = (
    "git -C {I} commit -m 'msg'",
    'git -C {I} commit -m "msg with spaces"',
    "git -C {I} commit -m msg",
    "git -C {I} commit -m ''",
    "git -C {I} commit -m fix:a@b%c+d=e,f",
    "git -C {I} commit -F {F}",
    "git -C {I} commit --allow-empty -m x",
    "git -C {I} commit -a -m x",
    "git -C {I} commit -m x --all -q --quiet --no-verify -n --allow-empty-message",
    "git -C {I} commit -m a -m 'b c' -F {F}",
    "git -C {I} add -A && git -C {I} commit -m x",
    "git -C {I} add path/to/f && git -C {I} commit -m x",
    "git -C {I} add . --all f.txt && git -C {I} commit -m x",
    "  git   -C\t{I}  commit  -m x  ",
)

# 免除テンプレートに一致しない入力 ({I} / {F} は同上、{O} はルート外 repo)。
TEMPLATE_DENY_CASES = (
    "/usr/bin/git -C {I} commit -m x",
    "'git' -C {I} commit -m x",
    "git -c a.b=c -C {I} commit -m x",
    "git -C {I} -c a.b=c commit -m x",
    "git -C {I} -C {I} commit -m x",
    "FOO=1 git -C {I} commit -m x",
    "git -C {I} add f && git -C {I}2 commit -m x",
    "git -C {I} commit -am x",
    "git -C {I} commit --author=x -m y",
    "git -C {I} commit --message=x",
    "git -C {I} commit",
    "git -C {I} commit --allow-empty",
    "git -C {I} commit -m",
    "git -C {I} commit -F relative.txt",
    "git -C {I} add -f x && git -C {I} commit -m y",
    "git -C {I} add ../x && git -C {I} commit -m y",
    "git -C {I} add /abs/x && git -C {I} commit -m y",
    "git -C {I} add f && git -C {I} add g && git -C {I} commit -m x",
    "git -C {I} add f || git -C {I} commit -m x",
    "git -C {I} commit -m x && git -C {I} commit -m y",
    'git -C {I} commit -m "a$b"',
    'git -C {I} commit -m "a!b"',
    'git -C {I} commit -m "a\\b"',
    'git -C {I} commit -m "a`id`"',
    "git -C {I} commit -m 'a'b",
    "git -C {I} commit -m a'b'",
    "git -C '{I}' commit -m x",
    'git -C "{I}" commit -m x',
    "git -C {I}/../iso commit -m x",
    "git -C {I}/. commit -m x",
    "git -C relative/iso commit -m x",
    "git -C {I} commit -m 'a\nb'",
    "git -C {I} commit -m x;",
    "git -C {I} commit -m x &",
    "git -C {I} commit -m x | cat",
    "git -C {I} commit -m x > /dev/null",
    "git -C {I} commit -m x # c",
    "git -C {I} commit -m $(id)",
    "git -C {I} commit -m ~x",
    "git -C {I} commit -m x&&git -C {I} commit -m y",
    "git -C {I} add f &&git -C {I} commit -m x",
    "cd {I} && git commit -m x",
    "git -C {I} -c x.y=z$IFS--git-dir={O}/.git commit -m x",
    "git -C {I} commit -m x && bash -c 'git -C {O} commit -m y'",
    "git -C {I} commit -m x && git -C {O} commit -am --dry-run",
)

# commit invocation を含まないため、hook end-to-end では検査対象外 (hook が素通しする)。
TEMPLATE_DENY_CASES_WITHOUT_COMMIT = frozenset({"git -C {I} add f"})


@unittest.skipUnless(shutil.which("bash"), "template judge requires bash")
class IsolatedCommitTemplateLexicalTest(unittest.TestCase):
    """判定器 `isolated_commit_template_target` を bash で直接呼ぶ字句規則の単体テスト。

    ファイルシステムを見ない関数なので、実在しない絶対パスで検査する。
    """

    ISO = "/tmp/isolated-root/iso"
    MESSAGE_FILE = "/tmp/isolated-root/msg.txt"
    OUTSIDE = "/tmp/outside/repo"

    def target(self, command: str) -> subprocess.CompletedProcess[str]:
        script = f'source "{GG_TEMPLATE_LIB}" && isolated_commit_template_target "$1"'
        return subprocess.run(
            ["bash", "-c", script, "bash", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def fill(self, template: str) -> str:
        return (
            template.replace("{I}", self.ISO)
            .replace("{F}", self.MESSAGE_FILE)
            .replace("{O}", self.OUTSIDE)
        )

    def test_template_allow_cases_print_the_target_dir(self) -> None:
        for template in TEMPLATE_ALLOW_CASES:
            command = self.fill(template)
            with self.subTest(command=command):
                result = self.target(command)

                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(f"{self.ISO}\n", result.stdout)

    def test_template_deny_cases_do_not_match(self) -> None:
        for template in (*TEMPLATE_DENY_CASES, *TEMPLATE_DENY_CASES_WITHOUT_COMMIT):
            command = self.fill(template)
            with self.subTest(command=command):
                result = self.target(command)

                self.assertEqual(1, result.returncode, result.stderr)
                self.assertEqual("", result.stdout)


class IsolatedCommitTemplateHookTest(IsolatedGitRootsTestBase):
    """免除テンプレートの hook end-to-end (allow は GG / AL、deny は GG)。"""

    HOOKS = (GG_COMMIT_HOOK, AL_COMMIT_HOOK)

    def setUp(self) -> None:
        super().setUp()
        self.message_file = self.base / "message.txt"
        self.message_file.write_text("message\n", encoding="utf-8")

    def fill(self, template: str) -> str:
        return (
            template.replace("{I}", str(self.iso_repo))
            .replace("{F}", str(self.message_file))
            .replace("{O}", str(self.outside_repo))
        )

    def test_template_allow_cases_are_exempted_by_both_hooks(self) -> None:
        for template in TEMPLATE_ALLOW_CASES:
            command = self.fill(template)
            for hook in self.HOOKS:
                with self.subTest(hook=hook.name, command=command):
                    result = self.run_hook(hook, command, roots=str(self.allowed_root))

                    self.assert_allowed(result)

    def test_template_deny_cases_are_denied_by_commit_hook(self) -> None:
        for template in TEMPLATE_DENY_CASES:
            command = self.fill(template)
            with self.subTest(command=command):
                result = self.run_hook(
                    GG_COMMIT_HOOK, command, roots=str(self.allowed_root)
                )

                self.assert_denied(result)


class CodexConfirmedBypassIsolatedRootsTest(IsolatedGitRootsTestBase):
    """免除判定をすり抜けることが実確認された形は免除しない (GG / AL 共通)。"""

    HOOKS = (GG_COMMIT_HOOK, AL_COMMIT_HOOK)

    def assert_denied_by_both_hooks(self, command: str) -> None:
        for hook in self.HOOKS:
            with self.subTest(hook=hook.name, command=command):
                result = self.run_hook(hook, command, roots=str(self.allowed_root))

                reason = self.deny_reason(result)
                if hook == AL_COMMIT_HOOK:
                    self.assertIn("repo override", reason)

    def test_config_value_with_ifs_switching_git_dir_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} -c x.y=z$IFS--git-dir="
            f"{self.outside_repo}/.git commit -m x"
        )

    def test_command_after_isolated_commit_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x && "
            f"bash -c 'git -C {self.outside_repo} commit -m y'"
        )

    def test_background_commit_with_concurrent_command_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x & "
            f"mv {self.iso_repo} {self.base / 'moved'}"
        )

    def test_attached_all_flag_misread_as_dry_run_is_denied(self) -> None:
        self.assert_denied_by_both_hooks(
            f"git -C {self.iso_repo} commit -m x && "
            f"git -C {self.outside_repo} commit -am --dry-run"
        )


class NewlineSuffixedPathIsolatedRootsTest(IsolatedGitRootsTestBase):
    """名前が改行で終わるディレクトリを、改行を落とした別パスとして検証しない。

    許可ルート配下に隔離 repo `iso` と、名前が改行で終わる兄弟 `iso<LF>` を置く。
    `iso<LF>` の `.git` はルート外 repo の `.git` への symlink。
    """

    def setUp(self) -> None:
        super().setUp()
        self.newline_dir = self.allowed_root / "iso\n"
        self.newline_dir.mkdir()
        (self.newline_dir / ".git").symlink_to(
            self.outside_repo / ".git", target_is_directory=True
        )

    def call_template_function(
        self, function: str, *args: str
    ) -> subprocess.CompletedProcess[str]:
        script = (
            f'source "{GG_TEMPLATE_LIB}" && {function} "$@" && '
            'printf "%s" "$_ICT_CANONICAL"'
        )
        env = dict(self.env)
        env[ISOLATED_ROOTS_ENV] = str(self.allowed_root)
        return subprocess.run(
            ["bash", "-c", script, "bash", *args],
            cwd=str(self.real_repo),
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def test_canonical_dir_rejects_path_ending_with_newline(self) -> None:
        result = self.call_template_function(
            "_ict_canonical_dir", str(self.newline_dir)
        )

        self.assertEqual(1, result.returncode, result.stderr)

    def test_canonical_dir_rejects_symlink_resolving_to_newline_suffixed_dir(
        self,
    ) -> None:
        link = self.allowed_root / "lnk"
        link.symlink_to(self.newline_dir, target_is_directory=True)

        result = self.call_template_function("_ict_canonical_dir", str(link))

        self.assertEqual(1, result.returncode, result.stderr)

    def test_canonical_dir_accepts_sibling_without_newline(self) -> None:
        result = self.call_template_function("_ict_canonical_dir", str(self.iso_repo))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(str(self.iso_repo.resolve()), result.stdout)

    def test_template_via_symlink_to_newline_suffixed_repo_is_denied(self) -> None:
        link = self.allowed_root / "lnk"
        link.symlink_to(self.newline_dir, target_is_directory=True)

        for hook in (GG_COMMIT_HOOK, AL_COMMIT_HOOK):
            with self.subTest(hook=hook.name):
                result = self.run_hook(
                    hook,
                    f"git -C {link} commit -m x",
                    roots=str(self.allowed_root),
                )

                reason = self.deny_reason(result)
                if hook == AL_COMMIT_HOOK:
                    self.assertIn("repo override", reason)


class ReftableIsolatedRootsTest(IsolatedGitRootsTestBase):
    """reftable 形式の隔離 repo は `reftable` ディレクトリの実体で判定する (GG / AL 共通)。"""

    HOOKS = (GG_COMMIT_HOOK, AL_COMMIT_HOOK)

    def setUp(self) -> None:
        super().setUp()
        self.reftable_repo = self.allowed_root / "reftable-iso"
        self.reftable_repo.mkdir()
        init = subprocess.run(
            ["git", "-C", str(self.reftable_repo), "init", "--ref-format=reftable"],
            check=False,
            capture_output=True,
            text=True,
            env=self.env,
        )
        if init.returncode != 0:
            self.skipTest(f"git が reftable 形式に未対応: {init.stderr.strip()}")
        self.git(self.reftable_repo, "symbolic-ref", "HEAD", "refs/heads/master")
        self.git(self.reftable_repo, "config", "user.name", "Marketplace Test")
        self.git(
            self.reftable_repo, "config", "user.email", "marketplace@example.invalid"
        )
        self.git(self.reftable_repo, "config", "commit.gpgsign", "false")
        self.git(self.reftable_repo, "commit", "--allow-empty", "-m", "base")
        self.assertEqual(
            "reftable",
            self.git(self.reftable_repo, "rev-parse", "--show-ref-format"),
        )

    def test_template_commit_into_reftable_repo_is_allowed(self) -> None:
        for hook in self.HOOKS:
            with self.subTest(hook=hook.name):
                result = self.run_hook(
                    hook,
                    f"git -C {self.reftable_repo} commit -m x",
                    roots=str(self.allowed_root),
                )

                self.assert_allowed(result)

    def test_reftable_dir_symlinked_outside_is_denied(self) -> None:
        reftable = self.reftable_repo / ".git" / "reftable"
        moved = self.base / "outside" / "reftable-store"
        shutil.move(str(reftable), str(moved))
        reftable.symlink_to(moved, target_is_directory=True)

        for hook in self.HOOKS:
            with self.subTest(hook=hook.name):
                result = self.run_hook(
                    hook,
                    f"git -C {self.reftable_repo} commit -m x",
                    roots=str(self.allowed_root),
                )

                reason = self.deny_reason(result)
                if hook == AL_COMMIT_HOOK:
                    self.assertIn("repo override", reason)


if __name__ == "__main__":
    unittest.main()
