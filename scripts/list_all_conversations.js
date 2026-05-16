// List all databases, conversations grouped by user.
// Usage:
//   docker exec rag-mongodb mongosh --quiet --file /tmp/list_all_conversations.js
//
// Users live across multiple DBs; we union them for lookup.

const SYSTEM_DBS = new Set(["admin", "config", "local"]);

// Build a global user lookup map (id -> {email,name,db})
const userMap = {};
db.adminCommand({ listDatabases: 1 }).databases.forEach((d) => {
    if (SYSTEM_DBS.has(d.name)) return;
    const odb = db.getSiblingDB(d.name);
    if (!odb.getCollectionNames().includes("users")) return;
    odb.users
        .find({}, { _id: 1, email: 1, name: 1 })
        .forEach((u) => {
            const id = String(u._id);
            if (!userMap[id]) {
                userMap[id] = {
                    email: u.email || "",
                    name: u.name || "",
                    sources: [d.name],
                };
            } else if (!userMap[id].sources.includes(d.name)) {
                userMap[id].sources.push(d.name);
            }
        });
});

print("============================================================");
print("DATABASES");
print("============================================================");
db.adminCommand({ listDatabases: 1 }).databases.forEach((d) => {
    if (SYSTEM_DBS.has(d.name)) return;
    const odb = db.getSiblingDB(d.name);
    const cols = odb.getCollectionNames().sort();
    print("- " + d.name + "  (collections: " + cols.length + ")");
    print("    " + cols.join(", "));
});

print("");
print("============================================================");
print("USERS  (union across all DBs)");
print("============================================================");
Object.keys(userMap)
    .sort((a, b) => userMap[a].email.localeCompare(userMap[b].email))
    .forEach((id) => {
        const u = userMap[id];
        print(
            "- " +
                (u.email || "(no email)") +
                "  | " +
                (u.name || "") +
                "  | id=" +
                id +
                "  | in: " +
                u.sources.join(",")
        );
    });

print("");
print("============================================================");
print("CONVERSATIONS  (chat_sessions per DB, grouped by user)");
print("============================================================");

db.adminCommand({ listDatabases: 1 }).databases.forEach((d) => {
    if (SYSTEM_DBS.has(d.name)) return;
    const odb = db.getSiblingDB(d.name);
    if (!odb.getCollectionNames().includes("chat_sessions")) return;

    const total = odb.chat_sessions.estimatedDocumentCount();
    print("");
    print("== DB: " + d.name + "  | chat_sessions total=" + total + " ==");

    const groups = odb.chat_sessions
        .aggregate([
            {
                $group: {
                    _id: "$user_id",
                    count: { $sum: 1 },
                    sessions: {
                        $push: {
                            _id: "$_id",
                            title: "$title",
                            updated_at: "$updated_at",
                            created_at: "$created_at",
                            msgs: { $size: { $ifNull: ["$messages", []] } },
                        },
                    },
                },
            },
            { $sort: { count: -1 } },
        ])
        .toArray();

    if (groups.length === 0) {
        print("  (no sessions)");
        return;
    }

    groups.forEach((g) => {
        const uid = g._id;
        const u = userMap[uid];
        const label = u
            ? u.email + " (" + (u.name || "") + ")"
            : "(unknown user)";
        print("  - " + label + "  | user_id=" + uid + "  | sessions=" + g.count);

        g.sessions
            .sort((a, b) => {
                const ta = a.updated_at ? new Date(a.updated_at).getTime() : 0;
                const tb = b.updated_at ? new Date(b.updated_at).getTime() : 0;
                return tb - ta;
            })
            .forEach((s) => {
                const ts = s.updated_at
                    ? new Date(s.updated_at).toISOString()
                    : (s.created_at ? new Date(s.created_at).toISOString() : "");
                const title = (s.title || "").substring(0, 60);
                print(
                    "      • " +
                        ts +
                        "  msgs=" +
                        s.msgs +
                        "  | " +
                        title +
                        "  | _id=" +
                        s._id
                );
            });
    });
});

print("");
print("Done.");
