/**
 * Node-RED settings for the Sparki ingestion worker.
 *
 * Key behaviors:
 *  - In dev: admin UI is enabled at http://localhost:1880 with bcrypt auth.
 *  - In prod: set NODERED_UI_ENABLED=false to disable the editor entirely
 *    (flows still run, but the UI returns 404).
 *  - Flow file lives at /data/flows.json (mounted from ./node-red/flows/).
 *
 * Reference: https://nodered.org/docs/user-guide/runtime/configuration
 */

const uiEnabled = (process.env.NODERED_UI_ENABLED || "true").toLowerCase() === "true";

module.exports = {
    // ─── Runtime ─────────────────────────────────────────────────────
    uiPort: process.env.PORT || 1880,
    uiHost: "0.0.0.0",
    flowFile: "flows.json",

    // ─── Editor / Admin UI ───────────────────────────────────────────
    // When uiEnabled=false, both the admin API and the editor are
    // completely disabled. Flows still execute.
    httpAdminRoot: uiEnabled ? "/" : false,
    httpNodeRoot: "/api",
    disableEditor: !uiEnabled,

    // Admin auth (only relevant when uiEnabled=true)
    adminAuth: uiEnabled ? {
        type: "credentials",
        users: [
            {
                username: process.env.NODERED_ADMIN_USER || "admin",
                password: process.env.NODERED_ADMIN_PASSWORD_HASH,
                permissions: "*",
            },
        ],
    } : undefined,

    // ─── Logging ─────────────────────────────────────────────────────
    logging: {
        console: {
            level: "info",      // change to "debug" for verbose troubleshooting
            metrics: false,
            audit: false,
        },
    },

    // ─── Flow file storage ───────────────────────────────────────────
    flowFilePretty: true,       // human-readable JSON, easier to diff in git

    // ─── Security: hide credentials in flow exports ──────────────────
    credentialSecret: process.env.NODERED_CREDENTIAL_SECRET ||
        "sparki-default-credential-secret-change-in-prod",

    // ─── Function node globals ───────────────────────────────────────
    // Make environment variables accessible from function nodes via
    // env.get("VAR_NAME"). We need this for Postgres + Influx config.
    functionGlobalContext: {},
    functionExternalModules: false,

    // ─── Editor theme ────────────────────────────────────────────────
    editorTheme: {
        projects: {
            enabled: false,
        },
        page: {
            title: "Sparki Ingestion",
        },
        header: {
            title: "Sparki Ingestion",
        },
    },
};
