import { defineStore } from 'pinia'
import { ref } from 'vue'

import { getAccessToken, setAccessToken } from '@/api/client'
import { fetchMe, login as loginRequest, type LoginResponse } from '@/api/endpoints'

const STORAGE_KEY = 'ap_session'

interface PersistedSession {
  tenantId: string
  displayName: string
  permissions: string[]
}

function readPersisted(): PersistedSession | null {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedSession>
    if (!parsed.tenantId || !parsed.displayName) return null
    return {
      tenantId: parsed.tenantId,
      displayName: parsed.displayName,
      permissions: parsed.permissions ?? [],
    }
  } catch {
    return null
  }
}

const persisted = readPersisted()

export const useSessionStore = defineStore('session', () => {
  const token = ref<string | null>(getAccessToken())
  const tenantId = ref<string>(persisted?.tenantId ?? '')
  const displayName = ref<string>(persisted?.displayName ?? '')
  const permissions = ref<string[]>(persisted?.permissions ?? [])

  function persist(): void {
    if (!token.value || !tenantId.value) {
      localStorage.removeItem(STORAGE_KEY)
      return
    }
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        tenantId: tenantId.value,
        displayName: displayName.value,
        permissions: permissions.value,
      } satisfies PersistedSession),
    )
  }

  function apply(result: LoginResponse): void {
    token.value = result.access_token
    tenantId.value = result.tenant_id
    displayName.value = result.display_name
    permissions.value = result.permissions
    setAccessToken(result.access_token)
    persist()
  }

  async function signIn(body: {
    tenant_code: string
    email: string
    password: string
  }): Promise<void> {
    apply(await loginRequest(body))
  }

  async function restore(): Promise<void> {
    if (!token.value) return
    try {
      const me = await fetchMe()
      tenantId.value = me.tenant_id
      displayName.value = me.display_name ?? displayName.value
      permissions.value = me.permissions
      persist()
    } catch {
      signOut()
    }
  }

  function signOut(): void {
    token.value = null
    tenantId.value = ''
    displayName.value = ''
    permissions.value = []
    setAccessToken(null)
    localStorage.removeItem(STORAGE_KEY)
  }

  function can(permission: string): boolean {
    return permissions.value.includes(permission)
  }

  return { token, tenantId, displayName, permissions, signIn, restore, signOut, can }
})
