"""Qdrant Writer: Upsert von PhotoRecords."""
from __future__ import annotations

import logging
import uuid

from ingest.ollama_client import TEXT_VECTOR_SIZE

logger = logging.getLogger(__name__)


class QdrantWriter:
    def __init__(
        self,
        url: str = "http://localhost:6333",
        collection: str = "photos",
        faces_collection: str = "faces",
    ):
        from qdrant_client import QdrantClient

        self.client = QdrantClient(url=url)
        self.collection = collection
        self.faces_collection = faces_collection
        self._ensure_collection()
        self._ensure_faces_collection()

    def _ensure_collection(self):
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        try:
            info = self.client.get_collection(self.collection)
            logger.info("Collection '%s' exists", self.collection)
            vectors = getattr(info.config.params, "vectors", None) or {}
            text_cfg = vectors.get("text") if isinstance(vectors, dict) else None
            size = getattr(text_cfg, "size", None)
            if size and size != TEXT_VECTOR_SIZE:
                raise RuntimeError(
                    f"Collection '{self.collection}' text vector size is {size}, "
                    f"expected {TEXT_VECTOR_SIZE}. Recreate the collection."
                )
            return
        except RuntimeError:
            raise
        except Exception:
            logger.info("Creating collection '%s'...", self.collection)

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                "face": VectorParams(size=512, distance=Distance.COSINE),
                "clip": VectorParams(size=768, distance=Distance.COSINE),
                "text": VectorParams(size=TEXT_VECTOR_SIZE, distance=Distance.COSINE),
            },
        )
        for field, schema in (
            ("person_ids", PayloadSchemaType.KEYWORD),
            ("scene_tags", PayloadSchemaType.KEYWORD),
            ("annotations", PayloadSchemaType.KEYWORD),
            ("folder_name", PayloadSchemaType.KEYWORD),
            ("channel", PayloadSchemaType.KEYWORD),
            ("location_key", PayloadSchemaType.KEYWORD),
            ("location_lc", PayloadSchemaType.KEYWORD),
            ("date", PayloadSchemaType.KEYWORD),
            ("taken_at", PayloadSchemaType.DATETIME),
            ("event_name", PayloadSchemaType.KEYWORD),
        ):
            try:
                self.client.create_payload_index(self.collection, field_name=field, field_schema=schema)
            except Exception as e:
                logger.debug("Payload index %s: %s", field, e)

    def _ensure_faces_collection(self):
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        try:
            self.client.get_collection(self.faces_collection)
            logger.info("Collection '%s' exists", self.faces_collection)
            return
        except Exception:
            logger.info("Creating collection '%s'...", self.faces_collection)
        self.client.create_collection(
            collection_name=self.faces_collection,
            vectors_config=VectorParams(size=512, distance=Distance.COSINE),
        )
        for field, schema in (
            ("person_id", PayloadSchemaType.KEYWORD),
            ("photo_id", PayloadSchemaType.KEYWORD),
            ("file_path", PayloadSchemaType.KEYWORD),
        ):
            try:
                self.client.create_payload_index(
                    self.faces_collection, field_name=field, field_schema=schema
                )
            except Exception as e:
                logger.debug("Faces index %s: %s", field, e)

    def upsert(self, record) -> None:
        from qdrant_client.models import PointStruct

        from ingest.provenance import channel

        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, record.photo_id))
        vectors = {}
        if record.face_embedding:
            vectors["face"] = record.face_embedding
        if record.clip_embedding:
            vectors["clip"] = record.clip_embedding
        if record.text_embedding:
            vectors["text"] = record.text_embedding
        payload = {
            "photo_id": record.photo_id,
            "file_path": record.file_path,
            "person_ids": record.person_ids,
            "face_count": record.face_count,
            "face_boxes": record.face_boxes,
            "date": record.date,
            "date_source": record.date_source,
            "date_confidence": record.date_confidence,
            "taken_at": getattr(record, "taken_at", None),
            "gps": record.gps,
            "exif": record.exif,
            "folder_name": record.folder_name,
            # Herkunftskanal -- trennt eigene Aufnahmen von Empfangenem,
            # Screenshots und Dokumenten. Aus dem Pfad abgeleitet, hier
            # gespeichert, damit sich danach filtern laesst.
            "channel": channel(record.file_path or ""),
            "subfolder": getattr(record, "subfolder", None),
            "folder_type": record.folder_type,
            "folder_people": record.folder_people,
            "sequence_in_folder": record.sequence_in_folder,
            "file_mtime": getattr(record, "file_mtime", None),
            "file_ctime": getattr(record, "file_ctime", None),
            "file_size": getattr(record, "file_size", None),
            "person_suggestions": getattr(record, "person_suggestions", None) or [],
            "location": getattr(record, "location", None),
            "location_key": getattr(record, "location_key", None),
            "location_lc": getattr(record, "location_lc", None),
            "location_source": getattr(record, "location_source", None),
            "scene_tags": record.scene_tags,
            "caption_de": record.caption_de,
            "caption_display": getattr(record, "caption_display", None),
            "caption_source": getattr(record, "caption_source", None),
            "caption_locked": bool(getattr(record, "caption_locked", False)),
            "person_names": getattr(record, "person_names", None) or [],
            "annotations": getattr(record, "annotations", None) or [],
            "event_name": getattr(record, "event_name", None),
            "event_excluded": bool(getattr(record, "event_excluded", False)),
            "file_warning": getattr(record, "file_warning", None),
            "ingested_at": record.ingested_at,
        }
        self.client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point_id, vector=vectors, payload=payload)],
            wait=True,
        )
        self.upsert_faces(record)

    #: Vom Menschen vergeben -- muss einen Re-Ingest ueberleben.
    FACE_PRESERVE = ("person_id", "person_name")

    def upsert_faces(self, record) -> None:
        from qdrant_client.models import PointStruct

        faces = getattr(record, "faces", None) or []
        if not faces:
            return
        prepared = []
        for i, face in enumerate(faces):
            vec = face.get("embedding")
            if not vec:
                continue
            face_key = f"{record.photo_id}:{i}:{face.get('box')}"
            fid = str(uuid.uuid5(uuid.NAMESPACE_DNS, face_key))
            prepared.append((fid, vec, face))
        if not prepared:
            return

        # Der Upsert ersetzt das ganze Payload -- ohne diesen Schritt loescht
        # jeder erneute Lauf saemtliche Personen-Zuordnungen.
        existing: dict[str, dict] = {}
        try:
            found = self.client.retrieve(
                collection_name=self.faces_collection,
                ids=[fid for fid, _, _ in prepared],
                with_payload=list(self.FACE_PRESERVE),
                with_vectors=False,
            )
            for point in found:
                kept = {
                    k: (point.payload or {}).get(k)
                    for k in self.FACE_PRESERVE
                    if (point.payload or {}).get(k)
                }
                if kept:
                    existing[str(point.id)] = kept
        except Exception as e:
            logger.warning("Could not preload face labels, they may be lost: %s", e)

        points = []
        for fid, vec, face in prepared:
            payload = {
                "face_id": fid,
                "photo_id": record.photo_id,
                "file_path": record.file_path,
                "box": face.get("box") or [],
                "score": face.get("score"),
                "landmarks": face.get("landmarks"),
                "frontality": face.get("frontality"),
                **existing.get(fid, {}),
            }
            points.append(PointStruct(id=fid, vector=vec, payload=payload))
        self.client.upsert(collection_name=self.faces_collection, points=points, wait=True)

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count
