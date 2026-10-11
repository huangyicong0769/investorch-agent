import type { ConfigSchema } from '../../api/management'
import type { JsonObject, JsonValue } from '../../api/types'

export function resolveSchema(schema: ConfigSchema, root: ConfigSchema): ConfigSchema {
  const target = schema.$ref?.startsWith('#/$defs/') ? root.$defs?.[schema.$ref.slice(8)] : undefined
  const resolved = target ? { ...target, ...schema, $ref: undefined } : schema
  const nonNull = resolved.anyOf?.find((option) => option.type !== 'null')
  return nonNull ? { ...resolveSchema(nonNull, root), ...resolved, anyOf: resolved.anyOf } : resolved
}

export function nullableSchema(schema: ConfigSchema): boolean {
  return Boolean(schema.anyOf?.some((option) => option.type === 'null'))
}

export function objectValue(value: JsonValue | undefined): JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

export function configValue(values: JsonObject, path: string): JsonValue | undefined {
  let value: JsonValue | undefined = values
  for (const part of path.split('.')) value = objectValue(value)[part]
  return value
}

export function fieldLabel(name: string): string {
  return name.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

export function schemaAtPath(root: ConfigSchema, path: string): ConfigSchema {
  let current = root
  for (const part of path.split('.')) {
    const schema = resolveSchema(current, root)
    const matchingPattern = Object.entries(schema.patternProperties ?? {}).find(([pattern]) => new RegExp(pattern).test(part))?.[1]
    current = schema.properties?.[part] ?? matchingPattern ?? (typeof schema.additionalProperties === 'object' ? schema.additionalProperties : {})
  }
  return current
}
