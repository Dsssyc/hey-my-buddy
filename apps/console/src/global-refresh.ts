/** A manual refresh waits for an overlapping poll before starting its own read. */
export async function waitForRead(isPending: () => boolean) {
  const deadline = Date.now() + 30_000;
  while (isPending()) {
    if (Date.now() >= deadline) throw new Error("现有读取尚未完成，请稍后重试。");
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}
