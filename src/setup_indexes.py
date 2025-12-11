"""Setup MongoDB Atlas Local search indexes."""

import asyncio
from pymongo import MongoClient
from src.settings import load_settings


def create_indexes():
    """Create vector and text search indexes for MongoDB Atlas Local."""
    settings = load_settings()
    
    print("="*60)
    print("MongoDB Atlas Local - Index Setup")
    print("="*60)
    print(f"Database: {settings.mongodb_database}")
    print(f"Chunks collection: {settings.mongodb_collection_chunks}")
    print(f"Embedding dimension: {settings.embedding_dimension}")
    print()
    
    # Connect to MongoDB
    client = MongoClient(settings.mongodb_uri)
    db = client[settings.mongodb_database]
    collection = db[settings.mongodb_collection_chunks]
    
    # Check if collection has documents
    count = collection.count_documents({})
    print(f"Documents in chunks collection: {count}")
    
    if count == 0:
        print("\nWARNING: No documents found. Indexes will be created but search will return empty.")
    
    # Create Vector Search Index
    print("\n[1] Creating Vector Search Index...")
    vector_index_def = {
        "name": settings.mongodb_vector_index,
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": settings.embedding_dimension,
                    "similarity": "cosine"
                }
            ]
        }
    }
    
    try:
        # Drop existing index if exists
        try:
            db.command({
                "dropSearchIndex": settings.mongodb_collection_chunks,
                "name": settings.mongodb_vector_index
            })
            print(f"  Dropped existing vector index: {settings.mongodb_vector_index}")
        except Exception:
            pass  # Index doesn't exist
        
        # Create new index
        result = db.command({
            "createSearchIndexes": settings.mongodb_collection_chunks,
            "indexes": [vector_index_def]
        })
        print(f"  Created vector index: {settings.mongodb_vector_index}")
        print(f"  Result: {result}")
    except Exception as e:
        print(f"  Error creating vector index: {e}")
    
    # Create Text Search Index
    print("\n[2] Creating Text Search Index...")
    text_index_def = {
        "name": settings.mongodb_text_index,
        "definition": {
            "mappings": {
                "dynamic": False,
                "fields": {
                    "content": {
                        "type": "string",
                        "analyzer": "lucene.standard"
                    }
                }
            }
        }
    }
    
    try:
        # Drop existing index if exists
        try:
            db.command({
                "dropSearchIndex": settings.mongodb_collection_chunks,
                "name": settings.mongodb_text_index
            })
            print(f"  Dropped existing text index: {settings.mongodb_text_index}")
        except Exception:
            pass  # Index doesn't exist
        
        # Create new index
        result = db.command({
            "createSearchIndexes": settings.mongodb_collection_chunks,
            "indexes": [text_index_def]
        })
        print(f"  Created text index: {settings.mongodb_text_index}")
        print(f"  Result: {result}")
    except Exception as e:
        print(f"  Error creating text index: {e}")
    
    # Wait for indexes to be ready
    print("\n[3] Waiting for indexes to be ready...")
    import time
    time.sleep(3)  # Give indexes time to build
    
    # List search indexes
    print("\n[4] Listing search indexes...")
    try:
        result = db.command({
            "listSearchIndexes": settings.mongodb_collection_chunks
        })
        if "cursor" in result and "firstBatch" in result["cursor"]:
            for idx in result["cursor"]["firstBatch"]:
                status = idx.get("status", "unknown")
                name = idx.get("name", "unknown")
                print(f"  - {name}: {status}")
        else:
            print(f"  Result: {result}")
    except Exception as e:
        print(f"  Error listing indexes: {e}")
    
    print("\n" + "="*60)
    print("Index setup complete!")
    print("="*60)
    
    client.close()


if __name__ == "__main__":
    create_indexes()
