// Copy chat sessions (conversations) from one user to another.
// Users live in USERS_DB; sessions live in SESSIONS_DB (current db).
// Usage:
//   docker exec rag-mongodb mongosh --quiet rag_test_law --file /tmp/copy_conversations.js

const USERS_DB = "rag_parhelion";
const SOURCE_QUERY = "mirjana.covic";
const TARGET_QUERY = "emanuel.plopu";

const usersDb = db.getSiblingDB(USERS_DB);
const findUser = (q) =>
    usersDb.users.findOne(
        { email: { $regex: q, $options: "i" } },
        { _id: 1, email: 1, name: 1 }
    );

const source = findUser(SOURCE_QUERY);
const target = findUser(TARGET_QUERY);

print("Source user: " + JSON.stringify(source));
print("Target user: " + JSON.stringify(target));

if (!source || !target) {
    throw new Error("Source or target user not found in users collection");
}

const sourceId = String(source._id);
const targetId = String(target._id);

const sessions = db.chat_sessions.find({ user_id: sourceId }).toArray();
print("Found " + sessions.length + " session(s) for source user " + source.email);

let copied = 0;
let skipped = 0;
const now = new Date();

for (const s of sessions) {
    const newId = UUID().toString().replace(/-/g, "");
    // chat_sessions._id is a string UUID per the Pydantic model
    const newSessionId =
        (typeof crypto !== "undefined" && crypto.randomUUID)
            ? crypto.randomUUID()
            : newId;

    // Skip if already cloned (idempotency: tag clones with cloned_from)
    const already = db.chat_sessions.findOne({
        user_id: targetId,
        cloned_from: s._id,
    });
    if (already) {
        skipped++;
        continue;
    }

    const clone = Object.assign({}, s);
    clone._id = newSessionId;
    clone.user_id = targetId;
    clone.cloned_from = s._id;
    clone.cloned_at = now;
    // keep created_at/updated_at as on the source so order is preserved

    db.chat_sessions.insertOne(clone);
    copied++;
    print(
        "  copied session " +
            s._id +
            " -> " +
            newSessionId +
            " (title: " +
            (s.title || "") +
            ", messages: " +
            ((s.messages && s.messages.length) || 0) +
            ")"
    );
}

print(
    "Done. copied=" +
        copied +
        " skipped(already cloned)=" +
        skipped +
        " total_source=" +
        sessions.length
);
