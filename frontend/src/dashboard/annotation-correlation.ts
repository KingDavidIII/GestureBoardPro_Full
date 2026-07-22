import type { AnnotatedFrameMessage } from "../protocol";
import type { GestureResultMessage } from "../protocol/messages";

type AnnotationMetadata = NonNullable<GestureResultMessage["annotation"]>;

export type AnnotationCorrelationUpdate =
  | { readonly kind: "none" }
  | { readonly kind: "clear" }
  | { readonly kind: "frame"; readonly frame: AnnotatedFrameMessage };

export type AnnotationCorrelationListener = (
  update: AnnotationCorrelationUpdate,
) => void;

export interface AnnotationCorrelationOptions {
  readonly subscriberErrorHandler?: (error: unknown) => void;
}

const none = (): AnnotationCorrelationUpdate => ({ kind: "none" });

/** Correlate one bounded pending result with one bounded pending binary frame. */
export class AnnotationCorrelation {
  private pendingMetadata: AnnotationMetadata | null = null;
  private pendingFrame: AnnotatedFrameMessage | null = null;
  private presentedSequence: number | null = null;
  private lastAcceptedSequence: number | null = null;
  private readonly listeners = new Set<AnnotationCorrelationListener>();
  private readonly subscriberErrorHandler: (error: unknown) => void;
  private destroyed = false;

  constructor(options: AnnotationCorrelationOptions = {}) {
    this.subscriberErrorHandler =
      options.subscriberErrorHandler ??
      ((error) =>
        console.error("Annotation correlation listener failed", error));
  }

  subscribe(listener: AnnotationCorrelationListener): () => void {
    if (this.destroyed) return () => undefined;
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  acceptResult(message: GestureResultMessage): AnnotationCorrelationUpdate {
    if (this.destroyed) return none();
    const annotation = message.annotation;
    if (!annotation || !annotation.enabled || !annotation.available)
      return this.publish(this.clearPresentation());
    if (annotation.sequence === undefined) return none();
    if (
      this.lastAcceptedSequence !== null &&
      annotation.sequence <= this.lastAcceptedSequence
    )
      return this.publish(none());
    this.pendingMetadata = annotation;
    return this.publish(this.correlate());
  }

  acceptFrame(frame: AnnotatedFrameMessage): AnnotationCorrelationUpdate {
    if (this.destroyed) return none();
    if (
      this.lastAcceptedSequence !== null &&
      frame.sequence <= this.lastAcceptedSequence
    )
      return this.publish(none());
    if (this.pendingMetadata?.sequence === frame.sequence) {
      this.pendingFrame = null;
      if (!this.metadataMatchesFrame(this.pendingMetadata, frame)) {
        this.pendingMetadata = null;
        return this.publish(none());
      }
      return this.publish(this.accept(frame));
    }
    if (
      this.pendingFrame === null ||
      frame.sequence > this.pendingFrame.sequence
    )
      this.pendingFrame = frame;
    return this.publish(none());
  }

  reset(): AnnotationCorrelationUpdate {
    if (this.destroyed) return none();
    const hadPresentation = this.presentedSequence !== null;
    this.pendingMetadata = null;
    this.pendingFrame = null;
    this.presentedSequence = null;
    this.lastAcceptedSequence = null;
    return this.publish(hadPresentation ? { kind: "clear" } : none());
  }

  clearPresentation(): AnnotationCorrelationUpdate {
    if (this.destroyed) return none();
    const hadPresentation = this.presentedSequence !== null;
    this.pendingMetadata = null;
    this.pendingFrame = null;
    this.presentedSequence = null;
    return hadPresentation ? { kind: "clear" } : none();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.pendingMetadata = null;
    this.pendingFrame = null;
    this.presentedSequence = null;
    this.lastAcceptedSequence = null;
    this.listeners.clear();
  }

  private correlate(): AnnotationCorrelationUpdate {
    if (
      this.pendingMetadata?.sequence === undefined ||
      this.pendingFrame?.sequence !== this.pendingMetadata.sequence
    )
      return none();
    const frame = this.pendingFrame;
    this.pendingFrame = null;
    if (!this.metadataMatchesFrame(this.pendingMetadata, frame)) {
      this.pendingMetadata = null;
      return none();
    }
    return this.accept(frame);
  }

  private accept(frame: AnnotatedFrameMessage): AnnotationCorrelationUpdate {
    this.pendingMetadata = null;
    this.presentedSequence = frame.sequence;
    this.lastAcceptedSequence = frame.sequence;
    return { kind: "frame", frame };
  }

  private metadataMatchesFrame(
    metadata: AnnotationMetadata,
    frame: AnnotatedFrameMessage,
  ): boolean {
    return (
      metadata.format === "jpeg" &&
      frame.mimeType === "image/jpeg" &&
      metadata.width === frame.width &&
      metadata.height === frame.height &&
      metadata.byte_length === frame.size
    );
  }

  private publish(
    update: AnnotationCorrelationUpdate,
  ): AnnotationCorrelationUpdate {
    if (this.destroyed) return none();
    if (update.kind !== "none")
      for (const listener of [...this.listeners]) {
        try {
          listener(update);
        } catch (error) {
          this.subscriberErrorHandler(error);
        }
      }
    return update;
  }
}
