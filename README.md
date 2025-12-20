# RecallHub - Intelligent Knowledge Base Search

Agentic RAG system combining MongoDB Atlas Vector Search with Pydantic AI for intelligent document retrieval.

## Features

- **Hybrid Search**: Combines semantic vector search with full-text keyword search using Reciprocal Rank Fusion (RRF)
  - Manual RRF implementation provides same quality as MongoDB's `$rankFusion` (which is in preview)
  - Concurrent execution for minimal latency overhead
- **Multi-Format Ingestion**: PDF, Word, PowerPoint, Excel, HTML, Markdown, Audio transcription
- **Intelligent Chunking**: Docling HybridChunker preserves document structure and semantic boundaries
- **Conversational CLI**: Rich-based interface with real-time streaming and tool call visibility
- **Multiple LLM Support**: OpenAI, OpenRouter, Ollama, Gemini
- **Cost Effective**: Runs entirely on MongoDB Atlas free tier (M0)

## Prerequisites

- Python 3.10+
- MongoDB Atlas account (**free M0 tier works perfectly!**)
- LLM provider API key (OpenAI, OpenRouter, etc.)
- Embedding provider API key (OpenAI or OpenRouter recommended)
- UV package manager

## Quick Start

### 1. Install UV Package Manager

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Clone and Setup Project

```bash
git clone https://github.com/coleam00/recallhub.git
cd recallhub

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # Unix/Mac
.venv\Scripts\activate     # Windows
uv sync
```

### 3. Set Up MongoDB Atlas

1. Go to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas/register) and create a free account
2. Click **"Create"** → Choose **M0 Free** tier → Select region → Click **"Create Deployment"**
3. **Quickstart Wizard** appears - configure security:
   - **Database User**: Create username and password (save these!)
   - **Network Access**: Click "Add My Current IP Address"
4. Click **"Connect"** → **"Drivers"** → Copy your connection string
   - Format: `mongodb+srv://username:<password>@cluster.mongodb.net/?appName=YourApp`
   - Replace `<password>` with your actual password

**Note**: Database (`rag_db`) and collections (`documents`, `chunks`) will be created automatically when you run ingestion in step 6.

### 4. Configure Environment Variables

```bash
# Copy the example file
cp .env.example .env
```

Edit `.env` with your credentials:
- **MONGODB_URI**: Connection string from step 3
- **LLM_API_KEY**: Your LLM provider API key (OpenRouter, OpenAI, etc.)
- **EMBEDDING_API_KEY**: Your API key for embeddings (such as OpenAI or OpenRouter)

### 5. Validate Configuration

```bash
uv run python -m src.test_config
```

You should see: `[OK] ALL CONFIGURATION CHECKS PASSED`

### 6. Run Ingestion Pipeline

```bash
# Add your documents to the documents/ folder
uv run python -m src.ingestion.ingest -d ./documents
```

This will:
- Process your documents (PDF, Word, PowerPoint, Excel, Markdown, etc.)
- Chunk them intelligently
- Generate embeddings
- Store everything in MongoDB (`rag_db.documents` and `rag_db.chunks`)

### 7. Create Search Indexes in MongoDB Atlas

**Important**: Only create these indexes AFTER running ingestion - you need data in your `chunks` collection first.

In MongoDB Atlas, go to **Database** → **Search and Vector Search** → **Create Search Index**

**1. Vector Search Index**
- Pick: **"Vector Search"**
- Database: `rag_db`
- Collection: `chunks`
- Index name: `vector_index`
- JSON:
```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    }
  ]
}
```

**2. Atlas Search Index**
- Click **"Create Search Index"** again
- Pick: **"Atlas Search"**
- Database: `rag_db`
- Collection: `chunks`
- Index name: `text_index`
- JSON:
```json
{
  "mappings": {
    "dynamic": false,
    "fields": {
      "content": {
        "type": "string",
        "analyzer": "lucene.standard"
      }
    }
  }
}
```

Wait 1-5 minutes for both indexes to build (status: "Building" → "Active").

### 8. Run the Agent

```bash
uv run python -m src.cli
```

Now you can ask questions and the agent will search your knowledge base!

## Project Structure

```
RecallHub/
├── src/                           # MongoDB implementation (COMPLETE)
│   ├── settings.py               # ✅ Configuration management
│   ├── providers.py              # ✅ LLM/embedding providers
│   ├── dependencies.py           # ✅ MongoDB connection & AgentDependencies
│   ├── test_config.py            # ✅ Configuration validation
│   ├── tools.py                  # ✅ Search tools (semantic, text, hybrid RRF)
│   ├── agent.py                  # ✅ Pydantic AI agent with search tools
│   ├── cli.py                    # ✅ Rich-based conversational CLI
│   ├── prompts.py                # ✅ System prompts
│   └── ingestion/
│       ├── chunker.py            # ✅ Docling HybridChunker wrapper
│       ├── embedder.py           # ✅ Batch embedding generation
│       └── ingest.py             # ✅ MongoDB ingestion pipeline
├── examples/                      # PostgreSQL reference (DO NOT MODIFY)
│   ├── agent.py                  # Reference: Pydantic AI agent patterns
│   ├── tools.py                  # Reference: PostgreSQL search tools
│   └── cli.py                    # Reference: Rich CLI interface
├── documents/                     # Document folder (13 sample documents included)
├── .claude/                       # Project documentation
│   ├── PRD.md                    # Product requirements
│   └── reference/                # MongoDB/Docling/Agent patterns
├── .agents/
│   ├── plans/                    # Implementation plans (all phases)
│   └── analysis/                 # Technical analysis & decisions
├── comprehensive_e2e_test.py      # ✅ Full E2E validation (10/10 passed)
└── pyproject.toml                # UV package configuration
```

## Technology Stack

- **Database**: MongoDB Atlas (Vector Search + Full-Text Search)
- **Agent Framework**: Pydantic AI 0.1.0+
- **Document Processing**: Docling 2.14+ (PDF, Word, PowerPoint, Excel, Audio)
- **Async Driver**: PyMongo 4.10+ with native async API
- **CLI**: Rich 13.9+ (terminal formatting and streaming)
- **Package Manager**: UV 0.5.0+ (fast dependency management)

## Hybrid Search Implementation

This project uses **manual Reciprocal Rank Fusion (RRF)** to combine vector and text search results, providing the same quality as MongoDB's `$rankFusion` operator while working on the **free M0 tier** (since $rankFusion is in preview it isn't available on the M0 tier).

### How It Works

1. **Semantic Search** (`$vectorSearch`): Finds conceptually similar content using vector embeddings
2. **Text Search** (`$search`): Finds keyword matches with fuzzy matching for typos
3. **RRF Merging**: Combines results using the formula: `RRF_score = Σ(1 / (60 + rank))`
   - Documents appearing in both searches get higher combined scores
   - Automatic deduplication
   - Standard k=60 constant (proven effective across datasets)

### Performance

- **Latency**: ~350-600ms per query (both searches run concurrently)
- **Accuracy**: 100% success rate on validation tests
- **Cost**: $0/month (works on free M0 tier)

## Usage Examples

### Interactive CLI

```bash
uv run python -m src.cli
```

**Example conversation:**
```
You: What is NeuralFlow AI's revenue goal for 2025?

  [Calling tool] search_knowledge_base
    Query: NeuralFlow AI's revenue goal for 2025
    Type: hybrid
    Results: 5
  [Search completed successfully]
