from __future__ import annotations

from typing import Any


class MilvusAdapterError(RuntimeError):
    pass


class MilvusAdapter:
    def __init__(self, uri: str, token: str | None = None, database: str = "default"):
        self.uri = uri
        self.token = token
        self.database = database

    def _client(self):
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise MilvusAdapterError(
                "Milvus support is not installed. Run: python -m pip install -e .[milvus]"
            ) from exc
        kwargs: dict[str, Any] = {"uri": self.uri, "db_name": self.database}
        if self.token:
            kwargs["token"] = self.token
        try:
            return MilvusClient(**kwargs)
        except Exception as exc:
            raise MilvusAdapterError(f"Unable to connect to Milvus: {exc}") from exc

    def discover_schema(self, collection: str) -> dict[str, Any]:
        client = self._client()
        try:
            description = client.describe_collection(collection_name=collection)
        except Exception as exc:
            raise MilvusAdapterError(
                f"Unable to describe collection {collection}: {exc}"
            ) from exc
        fields = []
        for field in description.get("fields", []):
            fields.append(
                {
                    "name": field.get("name"),
                    "type": str(field.get("type")),
                    "is_primary": bool(field.get("is_primary", False)),
                    "dimension": field.get("params", {}).get("dim"),
                }
            )
        path_candidates = [
            item["name"]
            for item in fields
            if item["name"]
            and any(token in item["name"].lower() for token in ["path", "uri", "url", "file"])
        ]
        vector_fields = [
            item["name"]
            for item in fields
            if "VECTOR" in item["type"].upper()
        ]
        return {
            "collection": collection,
            "database": self.database,
            "fields": fields,
            "path_candidates": path_candidates,
            "vector_fields": vector_fields,
            "processing_ready": bool(path_candidates and vector_fields),
        }
