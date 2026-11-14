#!/usr/bin/env bash
# Owner-run, once: prune the reconciled branches (docs/ops/branch-reconciliation-2026-11-13.md).
# The same list lives in ops/branch_ledger.yaml, which `maintain` prunes by itself once the ops
# token has Contents write; this script is the owner's equivalent and either may run first.
# tests/unit/test_branch_prune.py fails if this list and ops/branch_ledger.yaml ever differ.
# Each branch is deleted only if its live head still equals the recorded SHA. Superseded
# branches are tagged archive/<branch> at that SHA first. Needs `gh auth login` as the owner.
# Safe to re-run: missing branches and existing tags are skipped.
set -uo pipefail
O=kgsmith19
ok=0; skip=0; fail=0
prune() { local repo=$1 br=$2 sha=$3 mode=$4 live
  live=$(gh api "repos/$O/$repo/git/ref/heads/$br" --jq .object.sha 2>/dev/null) || { echo "skip (gone) $repo $br"; skip=$((skip+1)); return; }
  [ "$live" = "$sha" ] || { echo "SKIP (moved to $live) $repo $br"; skip=$((skip+1)); return; }
  if [ "$mode" = ARCH ]; then gh api -X POST "repos/$O/$repo/git/refs" -f ref="refs/tags/archive/$br" -f sha="$sha" >/dev/null 2>&1 || gh api "repos/$O/$repo/git/ref/tags/archive/$br" --jq .object.sha 2>/dev/null | grep -qx "$sha" || { echo "FAIL tag $repo $br"; fail=$((fail+1)); return; }; fi
  gh api -X DELETE "repos/$O/$repo/git/refs/heads/$br" >/dev/null && { echo "deleted $repo $br"; ok=$((ok+1)); } || { echo "FAIL delete $repo $br"; fail=$((fail+1)); }
}
prune hyperbolic-core ccr-3fd10670-dj781f d38da4609488b524c3702ac0d22d37de7c19024c DEL
prune hyperbolic-core ccr-70fd9e88-v8oqpz 24639a8db7efb2a25804f02f8cfe546162891b1e DEL
prune hyperbolic-core ccr-a3588a7e-gkdp4s 894862d4cf6396066fe2251940f221e0481a44c5 DEL
prune hyperbolic-core ccr-b18954ce-o770ss 3c780aa8346550ccdc08e5150a9d4e7259b43f9f DEL
prune hyperbolic-core claude/sleepy-lamport-rk45pp b83370dc73252317ce996b3718d4193ae1684618 DEL
prune hyperbolic-core feat/lifeos-535-identity-crosswalk 6a8a6ef89c8d6e1513cfcdc7350d95a4d5df8bf2 DEL
prune hyperbolic-core feat/lifeos-576-today-calm-ui 5670ea123572a0347c90b018c1450ebb4101c864 DEL
prune hyperbolic-core issue/391-stage63-adopt-v5 23b9017056be25e67876a18df6b255cc47dc89b6 DEL
prune hyperbolic-core issue/421-s3-verification-molds abc805ac3cb55218242b3c78f8c8f2814497855f DEL
prune hyperbolic-core issue/422-implementation-quality-semantics 9868a433052101c418dd15639801b6853d31251b DEL
prune hyperbolic-core issue/433-lifeos-writer-preflight 199de7b11bcd9a69dab80b1020ae6cd8c48b70ee DEL
prune hyperbolic-core issue/436-agent-app-identities-and-prompt-close d6031671565dd0734e7e479d15a623d4b1a9f484 DEL
prune hyperbolic-core issue/440-reviewer-mint-contents-read 9d37453d623c4d81544edde1b0d5bf015046035a DEL
prune hyperbolic-core issue/445-queue-guard 165f3a16746213010146877fd26d95cc92ae93bc DEL
prune hyperbolic-core issue/450-monorepo-closeout aeb69cfbbf19ae4abd0e77c4b5e70f533c248b6d DEL
prune hyperbolic-core issue/506-sqlite-kit f2402ff935bdec277ac8ebd90ebfd5a15598d9e7 DEL
prune hyperbolic-core issue/510-repoint-platform 56e8e29d342770b213ab88ad8ef60c3a8b449398 DEL
prune hyperbolic-core issue/531-today-command-center 5b025ed02fc152ca30b6dd5d56872416c6c764f2 DEL
prune hyperbolic-core issue/531-today-day-context a764cbcf8cf9893d6d07fc33850c02523bda9e48 DEL
prune hyperbolic-core issue/547-remove-ai-review 0780037cfc2d9c258ffd141922f5be81c9edc26d DEL
prune hyperbolic-core issue/551-consolidate-apps 842220ce1cd9bf5bbc406b9c1592a6d5bad2ad40 DEL
prune hyperbolic-core issue/559-local-first-activation 383f5b4bb80b8762fa9e662fb00debbace7add48 DEL
prune hyperbolic-core issue/560-local-readiness 383f5b4bb80b8762fa9e662fb00debbace7add48 DEL
prune hyperbolic-core issue/588-sync-receipts 1360da556126776344c914719d07de0e0e1456bc DEL
prune hyperbolic-core issue/589-sync-rollback 9933763dc787f1e19d817a6cd8c30958bdacdf17 DEL
prune hyperbolic-core audit/lifeos-consolidation-20261022 002dcb8a4d66a8beb362371ef85c5b112565574a ARCH
prune hyperbolic-core fix/lifeos-569-design-tokens 2b4278e87f6efd366e05aa3ac4d95635f21ab622 ARCH
prune hyperbolic-core issue/443-queue-guard-proof 001c7953c9f610345fd926f2bc379e9a41f5e6fd ARCH
prune hyperbolic-core issue/499-freeze-cloud-ops 407f65b328834547a6da0566de45e64104ddfb71 ARCH
prune hyper-agents ccr-1b50d880-9z1eh8 9a83d98edb2f75583800f1b93ad8ec51dcacfee3 DEL
prune hyper-agents ccr-70fd9e88-v8oqpz fd718bacfa2d4ef64176657bee95297a1c17b5e2 DEL
prune hyper-agents ccr-81dd9f1d-7hlvo3 0905c2563c90969e6c83cd20c23b226cae422fe6 DEL
prune hyper-agents ccr-b18954ce-o770ss 35e62b2294aedb79554eae67a335cbbb62d26ba0 DEL
prune hyper-agents fix/idempotent-secret-delete a838fabdacf646fa1d0fd1c6b276e037921fc2c4 DEL
prune hyper-agents issue/100-ops-broker-p2-p4 faf0e5a75d0db0aa6a5f9ebaf3b2c198b8e15ec6 DEL
prune hyper-agents issue/21-aes-conformance bcfe8d7494c666a54713f8985af0aeb0cc23eae6 DEL
prune hyper-agents issue/25-team-roles f124bc815c27137aadc10f5a3d6a6c69ebab1b27 DEL
prune hyper-agents issue/27-workflow-team 0f2ec05f530f014e37dd78e222ab1cf0a6d1ec13 DEL
prune hyper-agents issue/29-sqlite-run-store c8832f5804a606583f2bf88cafa39cc3101bed5a DEL
prune hyper-agents issue/31-controller-tracker 144a4d25751bbef2dab3ff9b96968d000c33252b DEL
prune hyper-agents issue/33-p05-review-verify 01b7fe61e42da669635fec481fb5b58d39a560ce DEL
prune hyper-agents issue/35-api-contracts d228d2c3eed9b8af070a60a79ac0c9ed21be8dbb DEL
prune hyper-agents issue/37-headless-json-cli c090e46f3ec6ff5640c5c339b5b12e0688a01225 DEL
prune hyper-agents issue/39-signed-webhook 0c1078af36491ebf7c99581acb6dc7efa3e49aca DEL
prune hyper-agents issue/41-local-sqlite-daemon 2b8967ffda26f01baebb81da1dcf5357c9dc6321 DEL
prune hyper-agents issue/43-agent-server-adapter 7228a5af3ea0c0cec6853a392cb78828c698c4a1 DEL
prune hyper-agents issue/45-compose-hook-installer 6e0476c330704abbe94cb510f0ab8991b66a0b6a DEL
prune hyper-agents issue/47-frozen-eval-fixtures 3eea7afc80769d947088a830fa59468ac5318a2f DEL
prune hyper-agents issue/49-adversarial-probes 1d5cdfec1cc3c953e0bb0b35817f2b84a6c81635 DEL
prune hyper-agents ops/broker-p1 bd6c26f4968283fb59a415a6132d0a09ebe49590 DEL
prune hyper-agents ccr-9b07541b-53w1e0 a317fb5bbeff3fd15baa6e3fd465f73d69b0c064 ARCH
prune hyper-agents ccr-d9c33ca1-119wwp 4f0726ec9447246dbc7a3ce3342c2c0a4750edb1 ARCH
prune hyper-agents issue/19-docker-policy 02c903f07c4ef658f0ea854bce2bbb3385afd9e8 ARCH
prune agent-engineering-standard ccr-1b50d880-9z1eh8 4a72f88d3db92451357e513ce3bbbbad950c5511 DEL
prune agent-engineering-standard issue/324-review-contract 4a72f88d3db92451357e513ce3bbbbad950c5511 DEL
prune agent-engineering-standard ccr-a3588a7e-gkdp4s 1d97dc2816245271adf17c49cfd157586abd3aba DEL
prune agent-engineering-standard ccr-d113909a-3mabiw e58c9b5865bf7145e28045e5e6acba109bde0941 DEL
prune agent-engineering-standard claude/sleepy-lamport-rk45pp 3c0e4ce026e70dbe053e7d3f83861d4f86c94111 DEL
prune agent-engineering-standard issue/279-session-start-adapter a4579cd86d1eed3e08a223f59915a0024a6f7f54 DEL
prune agent-engineering-standard issue/287-queue-guard e0eedd761c079b215fc7ec809c08f3bcd1fe18c5 DEL
prune agent-engineering-standard issue/321-aes-vnext-conformance 0761a02c215e437d812ee5b851eea10799e6def3 DEL
prune agent-engineering-standard issue/327-conformance-job-check 7a8bc3cea0c971ca1968220e63ca09e36ee29088 DEL
prune agent-engineering-standard issue/329-remove-ai-review 542234328192c5ef8228de516905c077a6bcfb2d DEL
prune agent-engineering-standard issue/281-queue-guard-proof 482db9c7228f7336a08abfac23dc735052a0f346 ARCH
prune agent-extensions claude/sleepy-lamport-rk45pp 8e1acaf1c50c7aa68901a81762c9d5d1906bd5c0 DEL
prune agent-extensions issue/36-queue-guard-proof 10b0bcdabe3d67e5a425e959578a9fdc33ba4a99 ARCH
prune hyper-agents ccr-a3588a7e-gkdp4s 9e7fd0f9ad5248bed7eea731e095e1e756699eb4 DEL
prune hyper-agents ccr-75b2b0cd-yfz3b3 185df3f33ef93bc2dfba1ec13ed3392d3a6e847e DEL
prune hyperbolic-core ccr-75b2b0cd-yfz3b3 86573b673680e8c06f87eb3c8e8f8c66351bbb4b DEL
prune hyperbolic-core ccr-e456a9de-6bvwul 359c86519180009f55268c281c0a6e6e0d83f39d DEL
prune agent-engineering-standard ccr-75b2b0cd-yfz3b3 025ac96534255815c4307d8b9325ef257706cd8f DEL
prune agent-engineering-standard ccr-94977308-r74df1 af7192af710084e0cee09897d9dbb34e01385eb7 DEL
prune agent-engineering-standard ccr-fe1c5349-19w17j 6762a7ad321184a1725b844dd953e5c59c1fb1e2 DEL
prune hyperbolic-core ccr-94977308-r74df1 dd5136c86996373ba50e0def5ce9584260560b6c DEL
prune hyperbolic-core issue/630-registry-needs-me 538f049c6c557cc0ca564fe528dfabe3510af1f8 DEL
prune hyperbolic-core issue/636-docs-sheets-drift 37cab35ca54c834caab7efd24d2b51974b837733 DEL
echo "deleted=$ok skipped=$skip failed=$fail"
