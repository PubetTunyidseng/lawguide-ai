"""External store ของตัวบท

- OpenSearchStore : ระบบภายนอกตัวหลัก (เหมือนที่ใช้ในวิชา) BM25 thai analyzer + vector bge-m3 รวมด้วย RRF
- LocalStore      : BM25 ในหน่วยความจำ (pythainlp) ใช้ตอนทดสอบ/สำรอง เมื่อยังไม่ได้เปิด OpenSearch
ทั้งสองตัวมี interface เดียวกัน: index(docs), search(query, k), get(section_no), all_section_nos()
"""
import json
import re

from . import config

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def norm_section_no(s: str) -> str:
    s = str(s).translate(THAI_DIGITS)
    s = re.sub(r"^\s*(มาตรา|ม\.)\s*", "", s)
    return re.sub(r"\s+", "", s)


def load_processed() -> list:
    p = config.PROCESSED_DIR / "sections.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"ยังไม่มี {p} — รัน python -m lawguide.ingest ก่อน")
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _tok(text: str) -> list:
    from pythainlp.tokenize import word_tokenize
    return [t for t in word_tokenize(text.lower(), engine="newmm", keep_whitespace=False) if t.strip()]


class LocalStore:
    name = "local-bm25"

    def __init__(self, docs: list | None = None):
        self.docs = []
        self.index(docs if docs is not None else load_processed())

    def index(self, docs: list):
        from rank_bm25 import BM25Okapi
        self.docs = list(docs)
        self.by_no = {d["section_no"]: d for d in self.docs}
        corpus = [_tok(f"{d['chapter']} {d['text']}") for d in self.docs]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, k: int):
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(_tok(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [{**self.docs[i], "score": round(float(scores[i]), 4), "retriever": "bm25"}
                for i in order if scores[i] > 0]

    def get(self, section_no: str):
        return self.by_no.get(norm_section_no(section_no))

    def all_section_nos(self) -> set:
        return set(self.by_no)


class OpenSearchStore:
    @property
    def name(self):
        return "opensearch-hybrid(bm25+vector)" if config.USE_VECTOR else "opensearch-bm25"

    def __init__(self):
        from opensearchpy import OpenSearch
        self.client = OpenSearch(hosts=[{"host": config.OPENSEARCH_HOST, "port": config.OPENSEARCH_PORT}],
                                 use_ssl=False, timeout=30)
        self.index_name = config.INDEX_NAME
        self._embedder = None

    @property
    def embedder(self):
        if self._embedder is None and config.USE_VECTOR:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(config.EMBED_MODEL)
        return self._embedder

    def index(self, docs: list):
        dim = self.embedder.get_sentence_embedding_dimension() if self.embedder else None
        props = {"section_no": {"type": "keyword"}, "law_id": {"type": "keyword"},
                 "chapter": {"type": "text", "analyzer": "thai"},
                 "text": {"type": "text", "analyzer": "thai"},
                 "payload": {"type": "object", "enabled": False}}
        settings = {}
        if dim:
            props["embedding"] = {"type": "knn_vector", "dimension": dim}
            settings = {"index.knn": True}
        if self.client.indices.exists(index=self.index_name):
            self.client.indices.delete(index=self.index_name)
        self.client.indices.create(index=self.index_name,
                                   body={"settings": settings, "mappings": {"properties": props}})
        vecs = self.embedder.encode([f"{d['chapter']} {d['text']}" for d in docs], batch_size=16,
                                    normalize_embeddings=True) if dim else [None] * len(docs)
        for d, v in zip(docs, vecs):
            body = {"section_no": d["section_no"], "law_id": d["law_id"], "chapter": d["chapter"],
                    "text": d["text"], "payload": d}
            if v is not None:
                body["embedding"] = v.tolist()
            self.client.index(index=self.index_name, id=d["id"], body=body)
        self.client.indices.refresh(index=self.index_name)

    def _bm25(self, query, k):
        r = self.client.search(index=self.index_name, body={
            "size": k, "query": {"multi_match": {"query": query, "fields": ["text", "chapter^0.5"]}}})
        return [h["_source"]["payload"] for h in r["hits"]["hits"]]

    def _knn(self, query, k):
        if not self.embedder:
            return []
        v = self.embedder.encode(query, normalize_embeddings=True).tolist()
        r = self.client.search(index=self.index_name, body={
            "size": k, "query": {"knn": {"embedding": {"vector": v, "k": k}}}})
        return [h["_source"]["payload"] for h in r["hits"]["hits"]]

    def search(self, query: str, k: int):
        lists = [("bm25", self._bm25(query, k * 2)), ("vector", self._knn(query, k * 2))]
        fused, meta = {}, {}
        for name, docs in lists:
            for rank, d in enumerate(docs):
                fused[d["id"]] = fused.get(d["id"], 0.0) + 1.0 / (config.RRF_K + rank + 1)
                meta.setdefault(d["id"], (d, []))[1].append(name)
        top = sorted(fused, key=lambda i: -fused[i])[:k]
        return [{**meta[i][0], "score": round(fused[i], 5), "retriever": "+".join(meta[i][1])} for i in top]

    def get(self, section_no: str):
        r = self.client.search(index=self.index_name, body={
            "size": 1, "query": {"term": {"section_no": norm_section_no(section_no)}}})
        hits = r["hits"]["hits"]
        return hits[0]["_source"]["payload"] if hits else None

    def all_section_nos(self) -> set:
        r = self.client.search(index=self.index_name, body={"size": 2000, "_source": ["section_no"],
                                                            "query": {"match_all": {}}})
        return {h["_source"]["section_no"] for h in r["hits"]["hits"]}


def get_store():
    if config.BACKEND == "local":
        return LocalStore()
    return OpenSearchStore()
