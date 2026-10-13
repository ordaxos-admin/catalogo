PRAGMA foreign_keys = ON;

-- Exceções de nome público em consolidações de galeria são explícitas,
-- imutáveis e vinculadas a um par canônico -> fonte já revisado.
CREATE TABLE IF NOT EXISTS model_gallery_member_approvals (
  source_model_id TEXT PRIMARY KEY REFERENCES models(id) ON DELETE RESTRICT,
  canonical_model_id TEXT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
  registry_family TEXT NOT NULL CHECK(trim(registry_family) <> ''),
  reason TEXT NOT NULL CHECK(trim(reason) <> ''),
  approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK(canonical_model_id <> source_model_id)
);

CREATE INDEX IF NOT EXISTS idx_model_gallery_member_approvals_canonical
ON model_gallery_member_approvals(canonical_model_id, source_model_id);

CREATE TRIGGER IF NOT EXISTS trg_gallery_member_approval_validate_insert
BEFORE INSERT ON model_gallery_member_approvals
BEGIN
  SELECT RAISE(ABORT, 'gallery approval requires active models in the same franchise folder')
  WHERE NOT EXISTS (
    SELECT 1
    FROM models canonical
    JOIN models source ON source.id = NEW.source_model_id
    WHERE canonical.id = NEW.canonical_model_id
      AND canonical.published = 1
      AND source.published = 1
      AND canonical.franchise_id = source.franchise_id
      AND COALESCE(canonical.folder_id, -1) = COALESCE(source.folder_id, -1)
  );

  SELECT RAISE(ABORT, 'gallery approval cannot create a gallery chain')
  WHERE EXISTS (
    SELECT 1 FROM model_gallery_members
    WHERE source_model_id = NEW.canonical_model_id
       OR canonical_model_id = NEW.source_model_id
  );
END;

CREATE TRIGGER IF NOT EXISTS trg_gallery_member_approval_immutable_update
BEFORE UPDATE ON model_gallery_member_approvals
BEGIN
  SELECT RAISE(ABORT, 'gallery approval is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_gallery_member_approval_delete_protected
BEFORE DELETE ON model_gallery_member_approvals
BEGIN
  SELECT RAISE(ABORT, 'gallery approval deletion requires an explicit migration');
END;

-- 0014 exigia nome público idêntico. Mantemos o guard e permitimos
-- divergência apenas quando o par exato possui aprovação auditada.
DROP TRIGGER IF EXISTS trg_gallery_members_validate_insert;
DROP TRIGGER IF EXISTS trg_gallery_members_validate_update;

CREATE TRIGGER trg_gallery_members_validate_insert
BEFORE INSERT ON model_gallery_members
BEGIN
  SELECT RAISE(ABORT, 'gallery canonical cannot already be a source')
  WHERE EXISTS (
    SELECT 1 FROM model_gallery_members
    WHERE source_model_id = NEW.canonical_model_id
  );

  SELECT RAISE(ABORT, 'gallery source cannot already be a canonical')
  WHERE EXISTS (
    SELECT 1 FROM model_gallery_members
    WHERE canonical_model_id = NEW.source_model_id
  );

  SELECT RAISE(ABORT, 'gallery members must share franchise folder and public name or explicit approval')
  WHERE NOT EXISTS (
    SELECT 1
    FROM models canonical
    JOIN models source ON source.id = NEW.source_model_id
    WHERE canonical.id = NEW.canonical_model_id
      AND canonical.franchise_id = source.franchise_id
      AND COALESCE(canonical.folder_id, -1) = COALESCE(source.folder_id, -1)
      AND (
        lower(trim(canonical.name)) = lower(trim(source.name))
        OR EXISTS (
          SELECT 1 FROM model_gallery_member_approvals approval
          WHERE approval.canonical_model_id = canonical.id
            AND approval.source_model_id = source.id
        )
      )
  );
END;

CREATE TRIGGER trg_gallery_members_validate_update
BEFORE UPDATE OF canonical_model_id, source_model_id ON model_gallery_members
BEGIN
  SELECT RAISE(ABORT, 'gallery canonical cannot already be a source')
  WHERE EXISTS (
    SELECT 1 FROM model_gallery_members
    WHERE source_model_id = NEW.canonical_model_id
      AND NOT (
        canonical_model_id = OLD.canonical_model_id
        AND source_model_id = OLD.source_model_id
      )
  );

  SELECT RAISE(ABORT, 'gallery source cannot already be a canonical')
  WHERE EXISTS (
    SELECT 1 FROM model_gallery_members
    WHERE canonical_model_id = NEW.source_model_id
      AND NOT (
        canonical_model_id = OLD.canonical_model_id
        AND source_model_id = OLD.source_model_id
      )
  );

  SELECT RAISE(ABORT, 'gallery members must share franchise folder and public name or explicit approval')
  WHERE NOT EXISTS (
    SELECT 1
    FROM models canonical
    JOIN models source ON source.id = NEW.source_model_id
    WHERE canonical.id = NEW.canonical_model_id
      AND canonical.franchise_id = source.franchise_id
      AND COALESCE(canonical.folder_id, -1) = COALESCE(source.folder_id, -1)
      AND (
        lower(trim(canonical.name)) = lower(trim(source.name))
        OR EXISTS (
          SELECT 1 FROM model_gallery_member_approvals approval
          WHERE approval.canonical_model_id = canonical.id
            AND approval.source_model_id = source.id
        )
      )
  );
END;
