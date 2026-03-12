# Embedding and Query Response System

This document provides a detailed description of how the RecallHub RAG (Retrieval-Augmented Generation) system processes documents for embedding and handles query responses.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Document Ingestion Pipeline](#document-ingestion-pipeline)
3. [Embedding Generation Process](#embedding-generation-process)
4. [Query Processing Pipeline](#query-processing-pipeline)
5. [Search Methods](#search-methods)
6. [Response Generation](#response-generation)
7. [Advanced Agent Workflow](#advanced-agent-workflow)
8. [System Prompts](#system-prompts)

---

## System Overview

RecallHub is an agentic RAG system that combines MongoDB Atlas Vector Search with LLM-powered conversational AI. The system processes documents through an ingestion pipeline, stores them with vector embeddings in MongoDB, and retrieves relevant context to answer user queries intelligently.

**Key Components:**
- **Document Ingestion Pipeline**: Converts documents to searchable chunks with embeddings
- **Embedding Service**: Generates vector representations of text using OpenAI or Ollama
- **Search Service**: Provides semantic, text, and hybrid search capabilities
- **Agent System**: Orchestrates search and response generation with tool-calling LLMs
- **Federated Search**: Enables multi-database search with access control

---

## Document Ingestion Pipeline

### Overview Flow

```
File Discovery
      │
      ▼
Format Detection
      │
      ├── Documents (PDF, DOCX, etc.) ──▶ Docling Converter
      ├── Images (PNG, JPG, etc.) ─────▶ OCR Pipeline
      ├── Audio (MP3, WAV, etc.) ──────▶ Whisper Transcription
      └── Video ───────────────────────▶ Frame Extraction + Audio
      │
      ▼
Markdown Output (unified format)
      │
      ▼
HybridChunker (Docling)
      │
      ├── Token-aware splitting
      ├── Document structure preservation
      ├── Semantic boundary respect
      └── Heading context inclusion
      │
      ▼
Embedding Generation
      │
      ▼
MongoDB Storage (documents + chunks collections)
```

### Phase 1: File Discovery and Format Detection

The ingestion pipeline scans configured document folders and classifies files by extension:
- **Documents**: PDF, DOC, DOCX, TXT, MD, HTML, XLSX, XLS, PPTX, PPT
- **Images**: PNG, JPG, JPEG, GIF, WEBP, SVG, BMP
- **Audio**: MP3, WAV, FLAC, M4A, OGG, WMA
- **Video**: MP4, AVI, MKV, MOV, WMV, WEBM

### Phase 2: Document Conversion

**Docling Converter** handles document processing:
- Extracts text while preserving structure (headings, tables, lists)
- Maintains document hierarchy and formatting
- Outputs normalized Markdown format
- Handles OCR for scanned documents when needed

**Audio Processing** uses Whisper:
- Transcribes audio content to text
- Preserves speaker identification when available
- Timestamps important segments

### Phase 3: Chunking with HybridChunker

The Docling HybridChunker provides intelligent document splitting:

**Capabilities:**
- **Token-aware**: Uses actual tokenizer (sentence-transformers/all-MiniLM-L6-v2) instead of character estimates
- **Structure preservation**: Respects document structure including headings, sections, and tables
- **Semantic boundaries**: Splits at paragraph, code block, and natural boundaries
- **Context inclusion**: Each chunk includes its heading hierarchy for better understanding

**Configuration Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_tokens` | 512 | Maximum tokens per chunk for embedding model |
| `chunk_size` | 1000 | Target characters (fallback mode) |
| `chunk_overlap` | 200 | Character overlap between chunks (fallback mode) |
| `merge_peers` | true | Merge small adjacent chunks |

**Contextualization Process:**
1. Docling parses document structure into a DoclingDocument
2. HybridChunker iterates through document elements
3. Each chunk receives contextualized text including:
   - Parent heading hierarchy (e.g., "Chapter 1 > Section A > Subsection")
   - Original content
   - Relevant metadata

**Fallback Mode:**
When DoclingDocument is not available, a simple sliding window approach is used:
1. Split content at sentence boundaries (., !, ?, \n)
2. Respect minimum chunk size (100 characters)
3. Apply overlap for context continuity

---

## Embedding Generation Process

### Overview

Embeddings convert text chunks into high-dimensional vectors that capture semantic meaning. The system uses OpenAI's embedding models as primary provider with Ollama as fallback.

### Embedding Flow

```
Document Chunks
      │
      ▼
Text Preprocessing
      │
      ├── Truncation (if exceeds max tokens)
      └── Batch grouping (up to 100 texts)
      │
      ▼
Primary Provider (OpenAI)
      │
      ├── Success ──▶ Return embeddings
      └── Failure ──▶ Retry with exponential backoff
              │
              ▼ (after max retries)
Fallback Provider (Ollama)
      │
      ▼
Embedding Vectors (1536-3072 dimensions)
```

### Supported Embedding Models

**OpenAI Models:**
| Model | Dimensions | Max Tokens |
|-------|------------|------------|
| text-embedding-3-large | 3072 | 8191 |
| text-embedding-3-small | 1536 | 8191 |
| text-embedding-ada-002 | 1536 | 8191 |

**Ollama Models (Fallback):**
| Model | Dimensions | Max Tokens |
|-------|------------|------------|
| nomic-embed-text | 768 | 8192 |
| mxbai-embed-large | 1024 | 512 |
| all-minilm | 384 | 256 |

### Resilience Configuration

The embedding service implements robust error handling:

**Retry Logic:**
- Maximum retries: 3
- Initial delay: 1.0 seconds
- Maximum delay: 30.0 seconds
- Exponential backoff multiplier: 2.0

**Retryable Errors:**
- Rate limit errors
- Timeout errors
- Connection errors
- Server errors (5xx status codes)

**Non-retryable Errors:**
- Authentication errors
- Invalid request errors (4xx except rate limits)

### Batch Processing

Embeddings are generated in batches for efficiency:
1. Chunks are grouped into batches (default: 100 per batch)
2. Each batch is sent to the embedding API
3. Progress callbacks report completion status
4. Control is yielded between batches to allow API responsiveness

### Metadata Attached to Embeddings

Each embedded chunk stores:
- `embedding_model`: Model used for generation
- `embedding_generated_at`: ISO timestamp of generation
- `token_count`: Actual token count from tokenizer

---

## Query Processing Pipeline

### Query Flow Overview

```
User Query
      │
      ▼
Query Embedding Generation
      │
      ▼
Search Execution
      │
      ├── Semantic Search ($vectorSearch)
      ├── Text Search ($search)
      └── Hybrid Search (both + RRF)
      │
      ▼
Document Lookup ($lookup)
      │
      ▼
Result Ranking & Deduplication
      │
      ▼
Context Assembly
      │
      ▼
LLM Response Generation
      │
      ▼
Response with Source Attribution
```

### Step 1: Query Embedding

The user's query text is converted to a vector using the same embedding model used during ingestion. This ensures the query vector is in the same semantic space as the document vectors.

### Step 2: Search Execution

Three search methods are available, each optimized for different query types.

---

## Search Methods

### Semantic Search (Vector Search)

Uses MongoDB's `$vectorSearch` aggregation operator to find conceptually similar content.

**Process:**
1. Query vector is compared against stored embeddings
2. Approximate Nearest Neighbor (ANN) search with HNSW index
3. Returns top-k most similar chunks by cosine similarity

**MongoDB Pipeline:**
```
$vectorSearch (numCandidates: 100, limit: N)
      │
      ▼
$lookup (join with documents collection)
      │
      ▼
$unwind (flatten document info)
      │
      ▼
$project (select fields + vectorSearchScore)
```

**Best For:**
- Conceptual/thematic queries
- Questions where exact keywords may not appear in documents
- Finding semantically related content

### Text Search (Full-Text Search)

Uses MongoDB Atlas Search with fuzzy matching for keyword-based retrieval.

**Process:**
1. Query text is tokenized and analyzed
2. Fuzzy matching allows for typos (max 2 edits, prefix length 3)
3. Returns chunks containing matching terms

**MongoDB Pipeline:**
```
$search (text query with fuzzy matching)
      │
      ▼
$limit (fetch count)
      │
      ▼
$lookup (join with documents collection)
      │
      ▼
$project (select fields + searchScore)
```

**Best For:**
- Exact term searches
- Technical jargon or specific names
- When you know the exact words used in documents

### Hybrid Search (Recommended Default)

Combines semantic and text search using Reciprocal Rank Fusion (RRF).

**Process:**
1. Execute semantic search and text search concurrently
2. Over-fetch results (2x requested count from each)
3. Apply RRF algorithm to merge and rank results
4. Return top-k merged results

**Reciprocal Rank Fusion Algorithm:**
```
For each document d appearing in result lists:
    RRF_score(d) = Σ(1 / (k + rank_i(d)))

Where:
- k = 60 (standard RRF constant)
- rank_i(d) = position of document d in result list i
```

**Enhanced RRF Adjustments:**
- **Cross-search boost**: +15% score for documents found in both searches
- **Content length penalties**:
  - < 50 chars: 50% penalty (likely incomplete)
  - < 100 chars: 30% penalty
  - < 200 chars: 15% penalty
  - > 500 chars: 5% bonus (rich content)
- **Score normalization**: Final scores normalized to 0.5-1.0 range

**Best For:**
- General-purpose queries
- When query type is uncertain
- Balancing semantic understanding with keyword precision

---

## Response Generation

### Simple Chat Pipeline

For direct chat interactions without advanced agent workflows:

**Process:**
1. User message received
2. System determines if search is needed (based on query type)
3. If search needed, LLM calls `search_knowledge_base` tool
4. Search results assembled into context
5. LLM generates response using context
6. Response returned with source attribution

### Tool Calling Loop

The chat system uses an iterative tool-calling pattern:

```
User Message
      │
      ▼
LLM Decision (with tools available)
      │
      ├── Tool Call Requested ──▶ Execute Tool
      │         │                      │
      │         └──────────────────────┘
      │                    ▼
      │            Add Result to Messages
      │                    │
      │         ◄──────────┘
      │
      └── Final Response ──▶ Return to User
```

**Maximum Iterations:** Configurable (default prevents infinite loops)

### Context Assembly

Search results are formatted for LLM consumption:
1. Top results extracted (typically top 5)
2. Document title and source included
3. Content truncated if needed (500-3000 characters per result)
4. Formatted as: `[Document Title]: content excerpt`

---

## Advanced Agent Workflow

The Federated Agent system provides sophisticated multi-step reasoning for complex queries.

### Agent Architecture

```
FederatedAgent (Coordinator)
      │
      ├── Orchestrator (Thinking Model - e.g., GPT-5.1, o1)
      │       ├── Analyze Phase
      │       ├── Plan Phase
      │       ├── Evaluate Phase
      │       └── Synthesize Phase
      │
      ├── WorkerPool (Fast Model - e.g., GPT-4o-mini)
      │       ├── Search Execution
      │       ├── Web Browsing
      │       └── Result Processing
      │
      └── FederatedSearch (Multi-Database Search)
              ├── Profile Documents
              ├── Personal Data
              ├── Cloud Storage
              └── Shared Resources
```

### Phase 1: Analysis

The orchestrator analyzes user intent with enhanced context extraction.

**Outputs:**
- Intent summary and classification
- Query type: FACTUAL, EXPLORATORY, COMPARATIVE, PROCEDURAL, AGGREGATE
- Named entity extraction (people, organizations, documents, dates, technical terms)
- Optimized search queries (primary + alternatives)
- Source priority determination
- Complexity assessment (1-5 scale)
- Multi-hop reasoning flag

**Conversation Context Processing:**
- Last 3 messages always included
- Earlier messages with significant term overlap included
- Maximum 10 context entries

### Phase 2: Planning

Creates an execution plan with targeted search tasks.

**Task Types:**
- `search_all`: Search all accessible sources
- `search_profile`: Search profile documents only
- `search_personal`: Search user's personal data
- `search_cloud`: Search cloud storage
- `web_search`: Search the web
- `browse_url`: Fetch specific URL content
- `summarize`: Summarize results

**Plan Structure:**
- Intent summary
- Reasoning explanation
- Execution strategy (parallel or sequential)
- Task list with priorities and dependencies
- Success criteria
- Maximum iterations

### Phase 3: Execution (Workers)

Worker pool executes planned tasks in parallel or sequence:
1. Tasks dispatched based on dependencies
2. Each worker executes search or browse operation
3. Results collected with quality assessment
4. Metadata tracked (duration, token usage)

### Phase 4: Evaluation

Orchestrator evaluates collected results:

**Decision Options:**
- `sufficient`: Enough information to answer
- `needs_refinement`: Additional searches needed
- `needs_web`: External information required
- `insufficient`: Cannot answer with available data

**Outputs:**
- Findings summary
- Gaps identified
- Follow-up tasks (if needed)
- Confidence score (0-1)

### Phase 5: Synthesis

Final answer generation from all collected information:
1. All document and web results compiled
2. Results sorted by relevance score
3. Content truncated to fit context window (~40,000 chars max)
4. Orchestrator generates comprehensive answer
5. Fallback generation if LLM fails

### Federated Search Access Control

The system supports multi-tenant search with access control:

**Data Source Types:**
| Type | Description | Access |
|------|-------------|--------|
| Profile | Shared organization documents | Profile members |
| Personal | User's private data (emails, etc.) | Owner only |
| Cloud Private | User's private cloud storage | Owner only |
| Cloud Shared | Shared cloud storage | Profile members |

**Source Priority Logic:**
- Identity/personal questions → Personal first
- Organization questions → Profile first
- File lookups → Cloud first
- External entities → Web search

---

## System Prompts

### Main RAG Agent System Prompt

Used for the CLI agent and basic chat:

```
You are a helpful assistant with access to a knowledge base containing documents, 
invoices, notes, transcripts, and business records.

IMPORTANT: You MUST use the search_knowledge_base tool to find information. 
You have NO built-in knowledge of the user's documents.

## Your Capabilities:
1. **Knowledge Base Search**: Use the `search_knowledge_base` tool to find relevant documents
2. **Information Synthesis**: Combine and summarize search results into helpful answers
3. **Conversation**: Engage naturally with users

## WHEN TO SEARCH (use search_knowledge_base):
- User asks about payments, invoices, amounts, transactions → SEARCH
- User asks about specific people, companies, or names → SEARCH  
- User asks about documents, records, notes, files → SEARCH
- User asks "how much", "when did", "what was" questions → SEARCH
- User asks about any business or work-related information → SEARCH

## WHEN NOT TO SEARCH:
- Greetings (hi, hello) → Just respond conversationally
- Questions about your capabilities → Answer directly
- General knowledge questions unrelated to documents → Answer if you know

## Search Strategy:
- Default to search_type="hybrid" for best results
- Use match_count=10 for comprehensive searches
- If first search returns nothing, try rephrasing the query

## Response Guidelines:
- Always cite the document source when providing information from searches
- If search returns no results, tell the user and suggest alternative search terms
- Be specific about amounts, dates, and names found in documents
- Answer in the same language as the documents when appropriate

Remember: When in doubt, SEARCH. The knowledge base contains the user's actual 
documents and records.
```

### Chat System Prompt with Tools

Used for the web chat interface with full tool access:

```
You are a helpful AI assistant with access to tools. You MUST use these tools 
to answer questions - NEVER respond without using tools first.

## Available Tools:

### 1. search_knowledge_base
Search internal documents using hybrid search (vector + text). The knowledge base 
contains documents about the user's company, projects, and internal information.
- **ALWAYS call this multiple times** with different queries (at least 2-3 searches)
- Start with broad context queries, then get specific
- If a search returns no results, TRY DIFFERENT TERMS - don't give up!
- When user says "my company" - search for company info, organization, business, etc.

### 2. browse_web  
Fetch and read content from a web URL. Use when you have a specific URL to visit.

### 3. web_search
Search the web using Brave Search to find URLs.

## CRITICAL RULES:

### Rule 1: NEVER give advice without searching first
**WRONG**: Explaining to user what they should do or look for
**RIGHT**: Actually searching and finding the information

### Rule 2: When user references "my company" or "our organization"
You MUST search the knowledge base first:
1. search_knowledge_base("company name organization")
2. search_knowledge_base("business overview about us")
3. Then search for the specific topic they asked about

### Rule 3: Don't give up after one search
- If search returns empty, try 2-3 MORE searches with different terms
- Try synonyms: "accounting" → "finance", "invoices", "bookkeeping"
- Try broader terms: "vendor" → "supplier", "partner", "company"

### Rule 4: Step-by-step questions require step-by-step tool usage
When user asks "go step by step":
1. Search for each piece of information separately
2. Use multiple tool calls in sequence
3. Gather all info before responding

Remember: You have access to the user's company documents. Search them! 
Multiple times! With different queries!
```

### Orchestrator Analysis Prompt

Used by the federated agent for intent analysis:

```
You are analyzing a user's question to understand their intent and plan the 
best search strategy.

User message: {user_message}
Conversation context: {conversation_history}

**Available Data Sources:**
- **profile**: Organization/company shared documents (policies, handbooks, reports)
- **cloud**: Cloud storage documents (Google Drive, Dropbox, WebDAV files)
- **personal**: User's private data (emails, personal documents, private files)
- **web**: Internet search via Brave Search + browse specific URLs

**CRITICAL ANALYSIS TASKS:**

1. **Intent Classification** - What type of question is this?
   - FACTUAL: Looking for specific facts ("Who is X?", "What is the address?")
   - EXPLORATORY: Looking for understanding ("How does X work?", "Explain Y")
   - COMPARATIVE: Looking for differences ("Compare A and B")
   - PROCEDURAL: Looking for steps ("How to do X?")
   - AGGREGATE: Looking for summaries ("Summarize all X")

2. **Entity Extraction** - Extract ALL named entities:
   - People: names, roles, titles, relationships
   - Organizations: companies, teams, departments
   - Documents: file names, document types
   - Dates: specific dates, time periods, deadlines
   - Technical terms: jargon, product names, acronyms

3. **Search Query Generation** - Create optimized search queries:
   - Use extracted entities as exact match terms (quotes)
   - Generate synonym variations for key concepts
   - Create both broad and narrow query variants

**Respond with JSON:**
{
    "intent_summary": "Clear statement of what user wants",
    "query_type": "FACTUAL|EXPLORATORY|COMPARATIVE|PROCEDURAL|AGGREGATE",
    "named_entities": {
        "people": ["person1", "person2"],
        "organizations": ["org1", "org2"],
        "documents": ["doc type or name"],
        "dates": ["date or period"],
        "technical_terms": ["term1", "term2"]
    },
    "search_queries": {
        "primary": "Main search query with key entities",
        "alternatives": ["synonym variation", "broader query", "narrower query"]
    },
    "sources_needed": ["profile", "cloud", "personal", "web"],
    "source_priority": "Which source is most likely to have the answer",
    "must_find": ["Critical information required"],
    "nice_to_have": ["Additional helpful context"],
    "complexity": 1-5,
    "requires_multi_hop": true/false,
    "reasoning": "Your strategic analysis"
}
```

### Orchestrator Synthesis Prompt

Used to generate final answers from search results:

```
You are generating a comprehensive answer to the user's question based on 
search results from multiple sources.

User's original question: {user_message}

Search Results (sorted by relevance):
{all_results}

**Instructions:**
1. Synthesize information from all relevant sources
2. Cite specific documents when making claims
3. If information is incomplete, acknowledge gaps
4. Answer in the same language as the user's question
5. Be specific about dates, amounts, and names
6. Structure your response clearly

**Generate your response now:**
```

---

## Performance Characteristics

### Typical Latencies

| Operation | Typical Duration |
|-----------|------------------|
| Query embedding generation | 100-300ms |
| Semantic search | 50-200ms |
| Text search | 50-150ms |
| Hybrid search (total) | 200-500ms |
| LLM response generation | 1-5 seconds |
| Full agent workflow | 5-30 seconds |

### Scaling Considerations

- **Embedding generation**: Batch processing reduces API calls
- **Vector search**: HNSW index provides sub-linear search time
- **Text search**: Atlas Search indexes provide fast full-text queries
- **Parallel execution**: Federated search queries multiple databases concurrently
- **Context limits**: Results truncated to fit LLM context windows

---

## Quality Optimization

### Search Quality Indicators

| Quality Level | Criteria |
|--------------|----------|
| Excellent | 5+ results, avg similarity > 0.8 |
| Good | 3+ results, avg similarity > 0.5 |
| Partial | 1+ results |
| Empty | No results found |

### Best Practices

1. **Chunk Size**: Keep chunks around 512 tokens for optimal embedding quality
2. **Overlap**: 200-character overlap prevents context loss at boundaries
3. **Hybrid Search**: Default to hybrid for best general-purpose results
4. **Multiple Queries**: Use query variations to improve recall
5. **Source Priority**: Match source types to query intent
6. **Iterative Refinement**: Use agent evaluation to identify gaps

---

*Last Updated: March 2026*
