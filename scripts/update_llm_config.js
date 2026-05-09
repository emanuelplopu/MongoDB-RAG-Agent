db = db.getSiblingDB('rag_parhelion');
db.llm_config.updateOne(
  {_id: 'provider_config'},
  {$set: {
    orchestrator_provider: 'ollama',
    orchestrator_model: 'gemma4:26b',
    worker_provider: 'ollama',
    worker_model: 'gemma4:e4b',
    updated_at: new Date().toISOString()
  }}
);
print('Updated LLM config:');
printjson(db.llm_config.findOne(
  {_id: 'provider_config'},
  {orchestrator_provider:1, orchestrator_model:1, worker_provider:1, worker_model:1}
));
