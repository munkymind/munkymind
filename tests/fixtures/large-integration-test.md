# Munkymind Integration Test — Large Document Fixture

This document is intentionally large (>10KB) to exercise the chunker's splitting logic.
It covers a broad technical topic so that embedding models receive meaningful text.

---

## Chapter 1: Distributed Systems Fundamentals

Distributed systems are collections of independent computers that appear to their users as a single coherent system. The field has grown dramatically over the past three decades as the internet scaled from thousands to billions of nodes.

### 1.1 The CAP Theorem

The CAP theorem, formulated by Eric Brewer in 2000 and proven by Gilbert and Lynch in 2002, states that a distributed system can provide at most two of three guarantees simultaneously:

- **Consistency**: Every read receives the most recent write or an error.
- **Availability**: Every request receives a response (not necessarily the most recent data).
- **Partition Tolerance**: The system continues to operate despite arbitrary message loss or partial failure of the network.

In practice, network partitions are unavoidable in real distributed systems, so designers must choose between consistency and availability when a partition occurs. Systems like HBase and Zookeeper prioritize CP; systems like CouchDB and Cassandra (in default config) prioritize AP.

### 1.2 Consensus Algorithms

Consensus algorithms allow distributed nodes to agree on a single value despite failures. The two most widely deployed are:

**Paxos** (Lamport, 1989): A two-phase protocol where a proposer selects a value and gets acceptors to agree. Paxos is notoriously difficult to implement correctly. Variants include Multi-Paxos (for log replication) and Fast Paxos (fewer round trips in the common case).

**Raft** (Ongaro & Ousterhout, 2014): Designed to be more understandable than Paxos. It decomposes consensus into three sub-problems:
1. Leader election: one server is elected leader at any time.
2. Log replication: the leader accepts log entries and replicates them to followers.
3. Safety: if any server has applied a log entry at a given index, no other server will ever apply a different command for that index.

Raft is the basis of systems like etcd (used by Kubernetes), CockroachDB, and TiKV.

### 1.3 Eventual Consistency

Eventual consistency is a consistency model used in distributed computing that informally guarantees that, if no new updates are made to a given data item, eventually all accesses to that item will return the last updated value. Amazon's DynamoDB and Apache Cassandra are prominent examples.

Eventual consistency is not a single model but a spectrum. Variations include:

- **Monotonic read consistency**: If a process reads the value of a data item x, any successive read operation on x by that process will always return that same value or a more recent value.
- **Monotonic write consistency**: A write operation by a process on a data item x is completed before any successive write operation on x by the same process.
- **Read-your-writes consistency**: A process always accesses the value of a data item x that was last written by it.
- **Session consistency**: Combining monotonic reads, monotonic writes, and read-your-writes, but scoped to a session.

---

## Chapter 2: Database Internals

Understanding how databases store and retrieve data is fundamental to building reliable systems.

### 2.1 Storage Engines

**B-Tree Storage**: The dominant storage structure for OLTP databases. A B-tree is a self-balancing tree data structure that maintains sorted data and allows searches, sequential access, insertions, and deletions in O(log n) time. PostgreSQL, MySQL InnoDB, and SQLite all use B-trees for their primary storage.

**LSM-Tree Storage**: Log-structured merge-trees write data sequentially to memory (memtable), then flush to disk as immutable SSTables. Compaction merges SSTables periodically. Used by LevelDB, RocksDB, Apache Cassandra, and ScyllaDB. LSM-trees trade read amplification for dramatically better write throughput.

**MVCC (Multi-Version Concurrency Control)**: Rather than locking rows for writes, MVCC stores multiple versions of a row and uses timestamps or transaction IDs to determine which version is visible to a given transaction. PostgreSQL, Oracle, and MySQL InnoDB implement MVCC. It enables non-blocking reads at the cost of more storage and a vacuum/garbage-collection process.

### 2.2 Query Optimization

Modern query optimizers transform declarative SQL into efficient execution plans through several stages:

1. **Parsing**: Convert SQL text into an abstract syntax tree (AST).
2. **Semantic Analysis**: Resolve table and column references, check types, and apply view definitions.
3. **Logical Planning**: Convert AST into a logical operator tree (project, filter, join, aggregate).
4. **Physical Planning**: Choose physical operators (hash join vs. nested-loop join vs. merge join), access paths (seq scan vs. index scan), and join orders.
5. **Cost Estimation**: Use statistics (row counts, column histograms, distinct value counts) to estimate the cost of each plan.
6. **Plan Selection**: Choose the plan with lowest estimated cost.

The join order problem is NP-hard in general, so optimizers use heuristics (greedy search, dynamic programming up to a certain number of tables, genetic algorithms) to find a good-enough plan.

### 2.3 Indexing Strategies

**Hash Indexes**: O(1) lookup by equality. Not useful for range queries. Used internally by PostgreSQL for some operations.

**B-Tree Indexes**: O(log n) lookup, supports equality, range, prefix, and sort operations. The workhorse index type.

**GIN (Generalized Inverted Index)**: For full-text search and JSONB containment queries in PostgreSQL. Stores a list of posting entries per key.

**BRIN (Block Range INdex)**: Extremely compact index for naturally ordered data (timestamps, auto-increment IDs). Stores the min/max value per block range rather than per row.

**Partial Indexes**: An index over a subset of rows matching a WHERE condition. Smaller and faster than a full index for queries that always include that condition.

**Covering Indexes**: An index that includes all columns needed to satisfy a query without accessing the heap. Eliminates the "table heap fetch" step.

---

## Chapter 3: Networking and Protocols

### 3.1 TCP/IP Deep Dive

TCP (Transmission Control Protocol) provides reliable, ordered, error-checked delivery of a stream of bytes between applications. Key mechanisms:

**Three-Way Handshake**: SYN → SYN-ACK → ACK establishes a connection. Each side initializes its sequence number randomly to prevent spoofing.

**Flow Control**: The receiver advertises a receive window (rwnd) indicating how much buffer space it has. The sender must not have more unacknowledged bytes in flight than min(cwnd, rwnd).

**Congestion Control**: TCP infers congestion from packet loss and round-trip time (RTT) increases. Algorithms:
- **Slow Start**: Begin with a small congestion window (cwnd), double it each RTT until ssthresh.
- **Congestion Avoidance**: Increase cwnd linearly (by 1 MSS per RTT) above ssthresh.
- **Fast Retransmit**: On receiving 3 duplicate ACKs, retransmit the lost segment immediately without waiting for a timeout.
- **CUBIC** (default in Linux): Uses a cubic function of time since last congestion event to set cwnd, independent of RTT.

**TCP BBR (Bottleneck Bandwidth and RTT)**: Google's 2016 congestion control algorithm. Rather than reacting to loss, it models the network's bottleneck bandwidth and minimum RTT and explicitly sets the sending rate. Dramatically improves throughput on high-bandwidth, high-latency links and in the presence of shallow buffers.

### 3.2 HTTP/2 and HTTP/3

**HTTP/2** introduced multiplexing (multiple requests over one TCP connection), header compression (HPACK), and server push. It eliminated head-of-line blocking at the HTTP layer, but TCP's ordering guarantee means a lost packet stalls all streams.

**HTTP/3** runs over QUIC (a UDP-based transport built into Chrome and now standardized as RFC 9000). QUIC provides:
- Independent stream delivery (a lost packet only stalls the affected stream, not all streams)
- 0-RTT connection resumption for repeat visitors
- Built-in TLS 1.3
- Connection migration (changing IP/port without reconnecting, critical for mobile)

### 3.3 Service Mesh and mTLS

In microservices architectures, service meshes (Istio, Linkerd, Consul Connect) intercept all inter-service traffic via a sidecar proxy (Envoy). They provide:
- **mTLS**: Mutual TLS between every pair of services, with certificates rotated automatically.
- **Traffic management**: Weighted routing, retries, circuit breaking, fault injection.
- **Observability**: Distributed tracing (Jaeger, Zipkin), metrics, and access logs — all without changing application code.

---

## Chapter 4: Observability

### 4.1 The Three Pillars

**Metrics**: Numeric measurements sampled at intervals. Time-series databases (Prometheus, InfluxDB, VictoriaMetrics) store them efficiently. Metrics are cheap to collect and aggregate but lose event-level detail.

**Logs**: Append-only records of discrete events. Structured logging (JSON lines) allows machines to parse and query them. Log aggregation stacks: ELK (Elasticsearch + Logstash + Kibana), Loki + Grafana, Datadog.

**Traces**: Records of end-to-end request flow across service boundaries. Each trace is a tree of spans. A span captures: service name, operation name, start/end timestamps, tags, and logs. OpenTelemetry has become the standard instrumentation API; backends include Jaeger, Zipkin, Honeycomb, and Lightstep.

### 4.2 SLOs, SLAs, and Error Budgets

**SLI (Service Level Indicator)**: A quantitative measure of service quality (e.g., request latency at p99, availability percentage).

**SLO (Service Level Objective)**: A target for an SLI (e.g., p99 latency < 200ms, 99.9% availability over a rolling 30-day window).

**SLA (Service Level Agreement)**: A contractual commitment between provider and customer, usually with financial consequences for violation. SLOs are internal targets, SLAs are external commitments.

**Error Budget**: The allowable amount of unreliability implied by an SLO. If your SLO is 99.9% availability, your error budget is 0.1% downtime — about 43 minutes per month. Error budgets are used to balance feature velocity against reliability: if the budget is nearly exhausted, freeze risky deploys.

---

## Chapter 5: Vector Databases and Embeddings

### 5.1 What Are Embeddings?

An embedding is a dense, low-dimensional numerical representation of high-dimensional data (text, images, audio, graphs). Embeddings capture semantic similarity: items that are similar in meaning are close in embedding space.

Text embeddings from models like OpenAI's text-embedding-3-small, Cohere's embed-v3, and open-source models (BGE, E5, Nomic) map text strings to vectors of 768–3072 dimensions.

### 5.2 Approximate Nearest Neighbor Search

Finding the exact nearest neighbors in high-dimensional spaces is O(n·d) — linear in the corpus size. For large corpora, approximate nearest neighbor (ANN) algorithms trade a small accuracy loss for dramatic speed gains.

**HNSW (Hierarchical Navigable Small World)**: Builds a multi-layer graph. Upper layers are sparse (long-range connections); lower layers are dense (short-range connections). Search navigates from the sparse top layer down to find approximate nearest neighbors. Used by Weaviate, Qdrant, and pgvector.

**IVF (Inverted File Index)**: Clusters vectors into k centroids (k-means), then searches only the nearest m clusters. Used by Faiss (Facebook AI Similarity Search).

**ScaNN (Scalable Nearest Neighbor)**: Google's ANN library using anisotropic quantization to minimize the reconstruction error for queries likely to be near a given vector.

### 5.3 ChromaDB Architecture

ChromaDB is an open-source embedding database designed for AI applications. Key components:

- **Collections**: Named groups of embeddings + documents + metadata. Analogous to a table.
- **HNSW Index**: ChromaDB uses hnswlib for ANN search. The index is built lazily and persisted to disk.
- **SQLite metadata store**: Document IDs, raw text, and user metadata are stored in SQLite for fast retrieval and filtering.
- **Embedding functions**: ChromaDB can embed text automatically using a pluggable embedding function (OpenAI, Cohere, SentenceTransformers) or accept pre-computed embeddings.

Querying returns: the top-k nearest documents by cosine similarity, their distances, their metadata, and optionally their raw text.

---

## Chapter 6: Large Language Models in Production

### 6.1 Inference Optimization

**Quantization**: Reduce model weights from FP32 or BF16 to INT8, INT4, or lower. Techniques: GPTQ (post-training quantization), AWQ (activation-aware weight quantization), GGUF/llama.cpp (CPU-friendly quantization formats).

**KV Cache**: Transformer inference computes key-value pairs for each token in the context. Caching and reusing these pairs for the prompt prefix eliminates redundant computation on repeat prefixes. Critical for multi-turn conversations.

**Continuous Batching**: Rather than processing one request at a time, batch multiple requests together at the token level (vLLM, TGI). New requests join the batch as slots open, maximizing GPU utilization.

**Speculative Decoding**: A small draft model generates k candidate tokens; the large target model verifies them in one forward pass. Achieves 2-3x throughput improvement when the draft model's predictions are often correct.

### 6.2 RAG (Retrieval-Augmented Generation)

RAG combines dense retrieval with generation. The standard pipeline:
1. Embed the user query.
2. Retrieve top-k chunks from the vector store by cosine similarity.
3. Stuff the retrieved chunks into the LLM's context window as grounding material.
4. Generate a response conditioned on the query + retrieved context.

**Chunking strategy matters**: Chunks too large dilute relevance; chunks too small lose context. Typical sweet spots: 512–2048 tokens per chunk with 10–20% overlap.

**Reranking**: A cross-encoder (Cohere Rerank, BGE-Reranker) re-scores the top-k retrieved chunks by jointly encoding the query and each chunk. More accurate than bi-encoder retrieval but too slow to run over the full corpus.

**HyDE (Hypothetical Document Embeddings)**: Ask the LLM to generate a hypothetical answer to the query, embed that answer, and retrieve documents similar to the hypothetical answer rather than the query directly. Often improves retrieval quality.

---

## Appendix: Australian Tropical Fruit Farming in Queensland

This section is here so that the integration test query ("Australian bananas tropical fruit Queensland") can find a relevant match in this large document, exercising end-to-end grounding even when the fixture is the only ingested document.

Queensland is Australia's primary producer of tropical and subtropical fruits. Key crops include:

- **Bananas**: Grown mainly in the Tully and Innisfail regions of Far North Queensland. Australia produces around 400,000 tonnes per year, almost entirely destined for the domestic market. The Cavendish variety dominates after Panama disease wiped out the Gros Michel in the 1950s.
- **Mangoes**: The Northern Territory and Queensland together account for most of Australia's mango production. The Kensington Pride (Bowen) variety is iconic; newer cultivars include R2E2, Calypso, and Honey Gold.
- **Pineapples**: Grown on the Sunshine Coast hinterland (Yandina, Bli Bli) and in South East Queensland. The Smooth Cayenne and Bethlehem varieties are most common.
- **Pawpaw/Papaya**: Grown in Far North Queensland. Australia exports SunUp (red-fleshed) and Sunrise varieties to Japan and other Asian markets.
- **Lychee**: Small but high-value crop in the Wet Tropics and Granite Belt regions.

Tropical fruit farming in Queensland faces challenges including:
- Cyclone risk (Cyclone Yasi in 2011 devastated banana crops)
- Panama Tropical Race 4 (TR4) disease threatening the Cavendish banana
- Fruit fly pressure requiring strict biosecurity protocols for interstate movement
- Water availability and irrigation costs in drier years

The Queensland Government's Department of Agriculture and Fisheries (DAF) provides research and extension services to the industry through facilities like the Mareeba Research Station.

---

*End of integration test fixture. Total size intentionally exceeds 10KB to trigger chunker splitting.*
