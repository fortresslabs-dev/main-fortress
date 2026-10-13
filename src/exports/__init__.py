"""Examination Evidence & Reports — exports package.

Per design doc §7. Six report types, deterministic render:

    snapshot.gather(type, as_of, bank_id) -> dict
    canonical.hash_envelope(snapshot)     -> (sha256, version_hash7)
    vocabulary.validate(snapshot)         -> None (raises on bad vocab)
    renderer.render_pdf(template, data)   -> bytes
    storage.put_artifact(...)             -> ExportArtifact row
    signoff.record(artifact_id, signer)   -> ExportSignoff row

Hash-before-render is non-negotiable — two artifacts with the same
content hash MUST have identical PDF bytes, but PDF bytes themselves
are never hashed (font subsetting + Chromium PDF non-determinism).
"""
__all__ = ["snapshot", "canonical", "vocabulary", "storage", "signoff",
           "renderer", "worker"]
