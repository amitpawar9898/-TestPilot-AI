import type { paths } from './schema'

type Method = 'get' | 'post' | 'patch' | 'put' | 'delete'
type StripPrefix<P> = P extends `/api${infer Relative}` ? Relative : never
type TemplatePath<P> = P extends `${infer Start}{${string}}${infer End}` ? `${Start}${string}${TemplatePath<End>}` : P
type OperationPath<M extends Method> = TemplatePath<StripPrefix<{
  [P in keyof paths]: M extends keyof paths[P] ? P : never
}[keyof paths]>>
type QueryPath<P> = P extends string ? P | `${P}?${string}` : never
export type ApiPath<M extends Method = Method> = QueryPath<OperationPath<M>>
export type FilePayload = { filename: string; media_type: string; content_base64: string }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError' }
}

export async function request<T>(path: ApiPath, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiError(response.status, error.detail || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const get = <T>(path: ApiPath<'get'>) => request<T>(path)
export const post = <T>(path: ApiPath<'post'>, body?: unknown) => request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const patch = <T>(path: ApiPath<'patch'>, body: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
export const del = <T>(path: ApiPath<'delete'>) => request<T>(path, { method: 'DELETE' })

export function saveFile(file: FilePayload) {
  const bytes = Uint8Array.from(atob(file.content_base64), c => c.charCodeAt(0))
  const url = URL.createObjectURL(new Blob([bytes], { type: file.media_type }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.filename; anchor.click()
  URL.revokeObjectURL(url)
}
