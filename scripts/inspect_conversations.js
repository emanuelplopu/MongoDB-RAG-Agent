// Inspect chat_sessions storage for both users.
const SOURCE_QUERY = "mirjana.covic";
const TARGET_QUERY = "emanuel.plopu";

const findUser = (q) =>
    db.users.findOne(
        { email: { $regex: q, $options: "i" } },
        { _id: 1, email: 1, name: 1 }
    );

const source = findUser(SOURCE_QUERY);
const target = findUser(TARGET_QUERY);
print("Source: " + JSON.stringify(source));
print("Target: " + JSON.stringify(target));

print("\nAll DBs:");
db.adminCommand({ listDatabases: 1 }).databases.forEach((d) => print("  " + d.name));

print("\nchat_sessions in current db (" + db.getName() + "): total=" + db.chat_sessions.estimatedDocumentCount());

print("\nDistinct user_id values in chat_sessions (current db):");
db.chat_sessions.distinct("user_id").forEach((u) => print("  " + u));

print("\nSample chat_session keys:");
const sample = db.chat_sessions.findOne();
if (sample) print("  " + Object.keys(sample).join(", "));

print("\nSearch chat_sessions across all dbs for these user ids:");
const targetIds = [source && source._id, target && target._id].filter(Boolean).map(String);
db.adminCommand({ listDatabases: 1 }).databases.forEach((d) => {
    const name = d.name;
    if (["admin", "config", "local"].includes(name)) return;
    const odb = db.getSiblingDB(name);
    const cols = odb.getCollectionNames();
    if (!cols.includes("chat_sessions")) return;
    targetIds.forEach((uid) => {
        const c = odb.chat_sessions.countDocuments({ user_id: uid });
        if (c > 0) print("  " + name + ".chat_sessions user_id=" + uid + " count=" + c);
    });
    const total = odb.chat_sessions.estimatedDocumentCount();
    print("  " + name + ".chat_sessions total=" + total);
});
