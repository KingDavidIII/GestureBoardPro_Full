export type ResourceCleanupOperation = readonly [
  operation: string,
  release: () => void,
];

export interface ResourceCleanupFailure {
  readonly operation: string;
  readonly error: unknown;
}

/** Describes every teardown operation that failed after all releases were attempted. */
export class ResourceCleanupError extends Error {
  readonly failures: readonly ResourceCleanupFailure[];

  constructor(scope: string, failures: readonly ResourceCleanupFailure[]) {
    const operations = failures.map(({ operation }) => operation).join(", ");
    super(`${scope} cleanup failed during ${operations}.`);
    this.name = "ResourceCleanupError";
    this.failures = Object.freeze(
      failures.map(({ operation, error }) =>
        Object.freeze({ operation, error }),
      ),
    );
  }
}

/** Run every teardown operation and report all failures without leaking later resources. */
export function releaseResourceOperations(
  scope: string,
  operations: readonly ResourceCleanupOperation[],
): void {
  const failures: ResourceCleanupFailure[] = [];

  for (const [operation, release] of operations) {
    try {
      release();
    } catch (error) {
      failures.push({ operation, error });
    }
  }

  if (failures.length > 0) throw new ResourceCleanupError(scope, failures);
}
