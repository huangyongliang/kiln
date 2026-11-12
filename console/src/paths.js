/**
 * Build API paths with encoded interpolations.
 * Tagged template: apiPath`/api/v1/skills/${id}/reject`
 */

export function apiPath(strings, ...values) {
  let path = "";
  for (let index = 0; index < values.length; index += 1) {
    path += strings[index] + encodeURIComponent(String(values[index]));
  }
  path += strings[strings.length - 1];
  return path;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isUuid(text) {
  return UUID.test(String(text ?? ""));
}
