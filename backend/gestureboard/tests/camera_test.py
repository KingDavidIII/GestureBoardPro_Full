from gestureboard.services.camera import CameraService

with CameraService() as camera:
    while True:
        frame = camera.read()

        import cv2

        cv2.imshow("GestureBoard Camera", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

print("Average FPS:", camera.fps())

cv2.destroyAllWindows()
