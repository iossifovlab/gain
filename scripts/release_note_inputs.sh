#!/usr/bin/env bash
# Print the inputs for composing release notes: one line per first-parent
# commit between two tags, with the merged PR and the issues it closes.
#
# Usage: scripts/release_note_inputs.sh [--repo <owner>/<name>] <prev tag> <new tag>
#
# Output (tab-separated, one line per first-parent commit, newest first):
#   <short sha>  #<PR>   <PR title>  closes #N, other/repo#M
#   <short sha>  no PR   <commit subject>
#
# Everything comes from GitHub through the gh CLI, so no local checkout of
# the repo is needed (e.g. --repo iossifovlab/gpf from anywhere). PR numbers
# and closed issues are read from GitHub, never parsed from commit subjects.
set -euo pipefail

usage() {
    echo "usage: $0 [--repo <owner>/<name>] <previous tag> <new tag>" >&2
    exit 2
}

repo="iossifovlab/gain"
positional=()
while [ $# -gt 0 ]; do
    case "$1" in
        --repo)
            [ $# -ge 2 ] || usage
            repo="$2"
            shift 2
            ;;
        --repo=*)
            repo="${1#--repo=}"
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            positional+=("$1")
            shift
            ;;
    esac
done
[ "${#positional[@]}" -eq 2 ] || usage
prev_tag="${positional[0]}"
new_tag="${positional[1]}"
owner="${repo%%/*}"
name="${repo#*/}"

for tag in "$prev_tag" "$new_tag"; do
    if ! gh api "repos/$repo/git/ref/tags/$tag" --silent 2>/dev/null; then
        echo "error: tag '$tag' does not exist in $repo" >&2
        exit 1
    fi
done

# All commits in prev..new as "<sha>\t<first parent sha>\t<subject>"; the
# subject goes last, unescaped, so read keeps any tabs in it.
commits_tsv="$(gh api --paginate \
    "repos/$repo/compare/$prev_tag...$new_tag?per_page=100" \
    --jq '.commits[] | ([.sha, (.parents[0].sha // "")] | @tsv)
        + "\t" + (.commit.message | split("\n")[0])')"

declare -A first_parent=()
declare -A subject_of=()
while IFS=$'\t' read -r sha parent subject; do
    [ -n "$sha" ] || continue
    first_parent["$sha"]="$parent"
    subject_of["$sha"]="$subject"
done <<< "$commits_tsv"

head_sha="$(gh api "repos/$repo/commits/$new_tag" --jq .sha)"

# Walk the first-parent chain from the new tag while it stays in the range.
chain=()
sha="$head_sha"
while [ -n "$sha" ] && [ -n "${first_parent[$sha]+set}" ]; do
    chain+=("$sha")
    sha="${first_parent[$sha]}"
done

# Resolve each commit to its merged PR (if any).
declare -A pr_of=()
declare -A title_of=()
prs=()
for sha in "${chain[@]}"; do
    # Prefer the merged PR whose merge commit is this very commit; fall back
    # to any merged PR that contains it.
    pr_line="$(gh api "repos/$repo/commits/$sha/pulls" --jq "
        [.[] | select(.merged_at != null)]
        | (map(select(.merge_commit_sha == \"$sha\")) + .)
        | first // empty
        | [.number, .title] | @tsv")"
    if [ -n "$pr_line" ]; then
        pr_of["$sha"]="${pr_line%%$'\t'*}"
        title_of["$sha"]="${pr_line#*$'\t'}"
        prs+=("${pr_of[$sha]}")
    else
        title_of["$sha"]="${subject_of[$sha]}"
    fi
done

# Closed issues of all PRs, in batched GraphQL queries.
declare -A closes_of=()
batch=50
for ((i = 0; i < ${#prs[@]}; i += batch)); do
    fields=""
    for pr in "${prs[@]:i:batch}"; do
        fields+=" pr$pr: pullRequest(number: $pr) { number closingIssuesReferences(first: 50) { nodes { number repository { nameWithOwner } } } }"
    done
    query="query(\$owner: String!, \$name: String!) { repository(owner: \$owner, name: \$name) {$fields } }"
    # A plain assignment, not a process substitution: set -e must see a
    # failed GraphQL call, or every PR would print with no closed issues.
    closes_tsv="$(gh api graphql -f query="$query" -f owner="$owner" -f name="$name" \
        --jq ".data.repository[] | [.number, ([.closingIssuesReferences.nodes[]
            | if .repository.nameWithOwner == \"$repo\"
              then \"#\(.number)\"
              else \"\(.repository.nameWithOwner)#\(.number)\" end]
            | join(\", \"))] | @tsv")"
    while IFS=$'\t' read -r pr closes; do
        [ -n "$pr" ] && closes_of["$pr"]="$closes"
    done <<< "$closes_tsv"
done

for sha in "${chain[@]}"; do
    short="${sha:0:9}"
    if [ -n "${pr_of[$sha]+set}" ]; then
        pr="${pr_of[$sha]}"
        closes="${closes_of[$pr]:-}"
        printf '%s\t#%s\t%s\t%s\n' "$short" "$pr" "${title_of[$sha]}" \
            "${closes:+closes $closes}"
    else
        printf '%s\tno PR\t%s\n' "$short" "${title_of[$sha]}"
    fi
done
