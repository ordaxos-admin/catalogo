from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from production_model_contract import (
    apply_production_model_contract,
    load_production_model_contract,
)


def contract_payload(models: list[dict]) -> dict:
    return {
        "version": 1,
        "source": {
            "databaseId": "db-test",
            "bookmark": "bookmark-test",
            "productionModels": len(models),
            "publishedModels": sum(int(bool(model["published"])) for model in models),
        },
        "models": models,
    }


def model_entry(*, model_id: str = "mdl_test", slug: str = "saga-heroi", published: int = 1) -> dict:
    return {
        "id": model_id,
        "slug": slug,
        "code": "TS-ABCDEF012345",
        "displayName": "Herói publicado",
        "variantName": "Modelo clássico",
        "categoryName": "Games",
        "categorySlug": "games",
        "franchiseName": "Saga",
        "franchiseSlug": "saga",
        "collection": "Heroi",
        "folderPath": ["Heroi"],
        "folderPathKey": "heroi",
        "published": published,
    }


def candidate_entry() -> dict:
    return {
        "id": "mdl_test",
        "slug": "saga-heroi-renomeado",
        "code": "TS-ABCDEF012345",
        "displayName": "Herói Renomeado",
        "variantName": "Nova variante",
        "categoryName": "Outra categoria",
        "categorySlug": "outra-categoria",
        "franchiseName": "Outra saga",
        "franchiseSlug": "outra-saga",
        "collection": "Outra",
        "folderPath": ["Outra"],
        "folderPathKey": "outra",
        "searchText": "conteúdo novo",
    }


class ProductionModelContractTests(unittest.TestCase):
    def write_contract(self, root: Path, models: list[dict]) -> Path:
        path = root / "contract.json"
        path.write_text(json.dumps(contract_payload(models), ensure_ascii=False), encoding="utf-8")
        return path

    def test_load_contract_validates_counts_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_contract(root, [model_entry()])
            loaded = load_production_model_contract(path)
            self.assertEqual(loaded["source"]["productionModels"], 1)
            self.assertEqual(loaded["source"]["publishedModels"], 1)
            self.assertEqual(loaded["slugOwners"], {"saga-heroi": "mdl_test"})
            self.assertEqual(loaded["byId"]["mdl_test"]["folderPath"], ["Heroi"])

    def test_active_contract_preserves_complete_public_identity(self) -> None:
        contract = {"mdl_test": model_entry()}
        candidate = candidate_entry()
        reconciled = apply_production_model_contract(candidate, contract)
        expected = model_entry()
        for field in (
            "slug",
            "displayName",
            "variantName",
            "categoryName",
            "categorySlug",
            "franchiseName",
            "franchiseSlug",
            "collection",
            "folderPath",
            "folderPathKey",
        ):
            self.assertEqual(reconciled[field], expected[field])
        self.assertEqual(candidate["slug"], "saga-heroi-renomeado")
        self.assertIn("herói", reconciled["searchText"])

    def test_contract_fails_closed_when_code_does_not_match_same_id(self) -> None:
        contract = {"mdl_test": model_entry()}
        candidate = candidate_entry()
        candidate["code"] = "TS-FFFFFFFFFFFF"
        with self.assertRaisesRegex(RuntimeError, "code incompatível"):
            apply_production_model_contract(candidate, contract)

    def test_retired_identity_is_previewable_but_cannot_return_to_final_bundle(self) -> None:
        retired = model_entry(slug="saga-heroi-antigo", published=0)
        contract = {"mdl_test": retired}
        candidate = candidate_entry()
        preview = apply_production_model_contract(candidate, contract, allow_retired=True)
        self.assertEqual(preview["slug"], "saga-heroi-antigo")
        self.assertEqual(preview["displayName"], "Herói publicado")
        self.assertEqual(preview["folderPathKey"], "heroi")
        with self.assertRaisesRegex(RuntimeError, "aposentada reapareceu"):
            apply_production_model_contract(candidate, contract)

    def test_duplicate_slug_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            second = model_entry(model_id="mdl_other", slug="saga-heroi")
            second["code"] = "TS-999999999999"
            path = self.write_contract(root, [model_entry(), second])
            with self.assertRaisesRegex(RuntimeError, "slug duplicado"):
                load_production_model_contract(path)

    def test_folder_path_key_must_match_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken = model_entry()
            broken["folderPathKey"] = "outra-pasta"
            path = self.write_contract(root, [broken])
            with self.assertRaisesRegex(RuntimeError, "folderPathKey divergente"):
                load_production_model_contract(path)


if __name__ == "__main__":
    unittest.main()
