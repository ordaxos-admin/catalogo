from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from resolve_external_identity_matches import (
    read_decisions,
    resolve_identity_matches,
    write_mapping_csv,
)


def candidate_report(*products: dict) -> dict:
    return {
        "version": 1,
        "policy": {
            "reviewOnly": True,
            "automaticMappings": 0,
        },
        "products": list(products),
    }


def product(
    incoming: str,
    *,
    candidates: list[str] | None = None,
    conflicts: list[dict] | None = None,
) -> dict:
    return {
        "incomingModel": incoming,
        "sourceProductKey": incoming.replace(" / ", ":"),
        "displayName": incoming.rsplit("/", 1)[-1].strip(),
        "category": "Games",
        "franchise": "Street Fighter",
        "reviewState": "candidate" if candidates else "no_candidate",
        "topTierCount": len(candidates or []),
        "candidates": [
            {
                "targetModel": target,
                "targetModelId": f"mdl-{index}",
                "displayName": target.rsplit("/", 1)[-1],
                "variantName": None,
                "reviewPriority": "name_review",
                "evidence": {},
            }
            for index, target in enumerate(candidates or [], 1)
        ],
        "scopeConflicts": list(conflicts or []),
    }


def decision(kind: str, *, target: str = "", reason: str = "revisão confirmada") -> dict[str, str]:
    return {"decision": kind, "target_model": target, "reason": reason}


class ResolveExternalIdentityMatchesTests(unittest.TestCase):
    def test_map_existing_requires_reported_candidate_and_stays_explicit(self) -> None:
        incoming = "vendor-a / product:42"
        target = "Games / Street Fighter / Ryu / ryu"
        report = candidate_report(product(incoming, candidates=[target]))

        result = resolve_identity_matches(
            report,
            {incoming: decision("map_existing", target=target, reason="mesma escultura e vistas")},
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["counts"]["mappedExisting"], 1)
        self.assertEqual(result["counts"]["newModels"], 0)
        self.assertEqual(result["policy"]["automaticMappings"], 0)
        self.assertEqual(result["mappings"][0]["incoming_model"], incoming)
        self.assertEqual(result["mappings"][0]["target_model"], target)
        self.assertEqual(result["mappings"][0]["decision_reason"], "mesma escultura e vistas")
        self.assertEqual(len(result["mappings"][0]["candidate_report_sha256"]), 64)

    def test_new_model_is_explicit_and_does_not_create_mapping(self) -> None:
        incoming = "vendor-a / product:50"
        report = candidate_report(product(incoming))

        result = resolve_identity_matches(
            report,
            {incoming: decision("new_model", reason="nenhuma ficha equivalente")},
        )

        self.assertEqual(result["counts"]["mappedExisting"], 0)
        self.assertEqual(result["counts"]["newModels"], 1)
        self.assertEqual(result["mappings"], [])
        self.assertEqual(result["newModels"][0]["incomingModel"], incoming)
        self.assertFalse(result["newModels"][0]["hadCandidates"])

    def test_operator_can_explicitly_keep_new_model_even_when_candidate_exists(self) -> None:
        incoming = "vendor-a / product:51"
        target = "Games / Street Fighter / Ryu / ryu"
        report = candidate_report(product(incoming, candidates=[target]))

        result = resolve_identity_matches(
            report,
            {incoming: decision("new_model", reason="variante escultórica distinta")},
        )

        self.assertEqual(result["mappings"], [])
        self.assertTrue(result["newModels"][0]["hadCandidates"])

    def test_mapping_target_must_be_in_candidate_report(self) -> None:
        incoming = "vendor-a / product:42"
        report = candidate_report(
            product(incoming, candidates=["Games / Street Fighter / Ryu / ryu"])
        )

        with self.assertRaisesRegex(RuntimeError, "não pertence aos candidatos auditados"):
            resolve_identity_matches(
                report,
                {
                    incoming: decision(
                        "map_existing",
                        target="Games / Street Fighter / Ken / ken",
                    )
                },
            )

    def test_every_incoming_product_requires_exactly_one_decision(self) -> None:
        first = "vendor-a / product:1"
        second = "vendor-a / product:2"
        report = candidate_report(product(first), product(second))

        with self.assertRaisesRegex(RuntimeError, "não cobrem exatamente"):
            resolve_identity_matches(report, {first: decision("new_model")})

        with self.assertRaisesRegex(RuntimeError, "não cobrem exatamente"):
            resolve_identity_matches(
                report,
                {
                    first: decision("new_model"),
                    second: decision("new_model"),
                    "vendor-a / product:extra": decision("new_model"),
                },
            )

    def test_two_external_products_cannot_map_to_same_public_model(self) -> None:
        first = "vendor-a / product:1"
        second = "vendor-a / product:2"
        target = "Games / Street Fighter / Ryu / ryu"
        report = candidate_report(
            product(first, candidates=[target]),
            product(second, candidates=[target]),
        )

        with self.assertRaisesRegex(RuntimeError, "mesmo modelo público"):
            resolve_identity_matches(
                report,
                {
                    first: decision("map_existing", target=target),
                    second: decision("map_existing", target=target),
                },
            )

    def test_scope_conflict_is_preserved_in_resolution_summary(self) -> None:
        incoming = "vendor-a / product:70"
        conflict = {
            "sha256": "a" * 64,
            "targetModel": "Filmes & Séries / Outra / Modelo / modelo",
        }
        report = candidate_report(product(incoming, conflicts=[conflict]))

        result = resolve_identity_matches(
            report,
            {incoming: decision("new_model", reason="imagem compartilhada, produto distinto")},
        )

        self.assertEqual(result["counts"]["productsWithScopeConflictReviewed"], 1)
        self.assertEqual(result["newModels"][0]["scopeConflictCount"], 1)

    def test_candidate_report_must_preserve_review_only_policy(self) -> None:
        incoming = "vendor-a / product:42"
        report = candidate_report(product(incoming))
        report["policy"]["automaticMappings"] = 1

        with self.assertRaisesRegex(RuntimeError, "review-only"):
            resolve_identity_matches(report, {incoming: decision("new_model")})

    def test_decision_csv_requires_reason_and_exact_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.csv"
            path.write_text(
                "incoming_model,decision,target_model,reason\n"
                "vendor-a / product:42,map_existing,Games / Street Fighter / Ryu / ryu,mesma escultura\n",
                encoding="utf-8",
            )
            decisions = read_decisions(path)
            self.assertEqual(decisions["vendor-a / product:42"]["decision"], "map_existing")

            path.write_text(
                "incoming_model,decision,target_model,reason\n"
                "vendor-a / product:42,new_model,,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "sem justificativa"):
                read_decisions(path)

            path.write_text(
                "incoming_model,decision,target_model\n"
                "vendor-a / product:42,new_model,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "colunas de decisões divergentes"):
                read_decisions(path)

    def test_mapping_csv_is_compatible_with_plan_mapping_contract(self) -> None:
        incoming = "vendor-a / product:42"
        target = "Games / Street Fighter / Ryu / ryu"
        report = candidate_report(product(incoming, candidates=[target]))
        result = resolve_identity_matches(
            report,
            {incoming: decision("map_existing", target=target)},
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-identity-map.csv"
            write_mapping_csv(path, result["mappings"])
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("incoming_model,target_model,candidate_report_sha256,decision_reason", text)
            self.assertIn(incoming, text)
            self.assertIn(target, text)


if __name__ == "__main__":
    unittest.main()
