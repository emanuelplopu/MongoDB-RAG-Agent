// MongoDB Atlas Local - Initialization Script
// This script runs automatically when the MongoDB container starts
// It creates the required search indexes for RAG functionality

// Wait for database to be ready
print("Initializing MongoDB RAG Agent database...");

// Switch to the RAG database
db = db.getSiblingDB('rag_db');

// Create collections if they don't exist
db.createCollection('documents');
db.createCollection('chunks');

print("Collections created: documents, chunks");

// Note: Vector and Atlas Search indexes must be created after data is ingested
// The mongodb-atlas-local image supports creating these indexes via the Atlas CLI
// or they will be created automatically when the first document is ingested

print("=".repeat(50));
print("MongoDB RAG Agent database initialized!");
print("");
print("IMPORTANT: After ingesting documents, create search indexes:");
print("");
print("1. Vector Search Index (run in mongosh):");
print("   db.chunks.createSearchIndex({");
print('     name: "vector_index",');
print("     type: \"vectorSearch\",");
print("     definition: {");
print("       fields: [{");
print('         type: "vector",');
print('         path: "embedding",');
print("         numDimensions: 1536,");
print('         similarity: "cosine"');
print("       }]");
print("     }");
print("   });");
print("");
print("2. Text Search Index:");
print("   db.chunks.createSearchIndex({");
print('     name: "text_index",');
print("     definition: {");
print("       mappings: {");
print("         dynamic: false,");
print("         fields: {");
print("           content: {");
print('             type: "string",');
print('             analyzer: "lucene.standard"');
print("           }");
print("         }");
print("       }");
print("     }");
print("   });");
print("");
print("=".repeat(50));
