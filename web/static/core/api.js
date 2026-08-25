/* Der eine Weg zum Backend. */

export async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export function cropUrl(faceId) {
  return `/api/faces/${encodeURIComponent(faceId)}/crop`;
}

/** `size` wird serverseitig auf 160/320/640/1280 gerundet -- hier dieselben Stufen. */
export function thumbUrl(photoId, size = 320) {
  return `/api/photos/${encodeURIComponent(photoId)}/thumb?size=${size}`;
}
