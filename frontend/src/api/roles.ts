/**
 * Role helpers shared by pages and the layout.
 *
 * `is_admin` marks a superadmin who may change every system setting.
 * `is_org_admin` marks an organisation admin who manages the user accounts
 * and profile access of their own organisation, but never superadmin accounts
 * or system configuration (prompts, models, ingestion, backups, ...).
 */
export interface RoleFlags {
  is_admin?: boolean | null
  is_org_admin?: boolean | null
}

export function isSuperAdmin(user?: RoleFlags | null): boolean {
  return Boolean(user?.is_admin)
}

export function isOrgAdmin(user?: RoleFlags | null): boolean {
  return !isSuperAdmin(user) && Boolean(user?.is_org_admin)
}

/** True for everyone allowed to open user management. */
export function canManageUsers(user?: RoleFlags | null): boolean {
  return isSuperAdmin(user) || Boolean(user?.is_org_admin)
}

/**
 * True for everyone allowed to open the Profiles page.
 *
 * Organisation admins see the profiles they have access to and may rename
 * them and pick their document folders (below the server's document roots).
 * Creating or deleting profiles, changing a profile's database and the
 * access matrix stay with superadmins (`isSuperAdmin`).
 */
export function canManageProfiles(user?: RoleFlags | null): boolean {
  return isSuperAdmin(user) || Boolean(user?.is_org_admin)
}
