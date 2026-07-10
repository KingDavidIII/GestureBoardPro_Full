import cv2

from gestureboard.services.camera import CameraService
from gestureboard.services.landmark_processor import LandmarkProcessor

with LandmarkProcessor() as processor, CameraService() as camera:
    while True:
        frame = camera.read().frame

        annotated, hands = processor.process(frame)

        cv2.putText(
            annotated,
            f"Hands: {len(hands)}",
            (15, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2,
        )

        cv2.imshow("GestureBoard Pro", annotated)

        key = cv2.waitKey(1)

        if key == ord("q"):
            break

cv2.destroyAllWindows()
