import io
import time
import numpy as np
from picamera import PiCamera
import picamera.array
import cv2   
from datetime import datetime
import paho.mqtt.client as mqtt
import os
import subprocess
import requests

class MJpegStreamCam:
    def __init__(self, framerate=25, width=640, height=480):
        self.size = (width, height)
        self.framerate = framerate
        self.frames_to_save = [] # 프레임 저장 공간
        self.max_files = 5  # 최대 5개 (약 50초)의 영상 관리
        self.save_directory = "/home/pi/workspace/iot_server/media/temp_video" # 프레임 to .mp4 변환 후 저장할 폴더

        self.camera = PiCamera()
        self.camera.rotation = 0
        self.camera.resolution = self.size
        self.camera.framerate = self.framerate

        self.client = mqtt.Client()
        self.host_id = '172.30.1.75'
        self.port = 1883
        self.istilt=False

        try:
            self.client.on_connect = self.on_connect
            self.client.on_message = self.on_message
            self.client.connect(self.host_id, self.port, 60)
            self.client.loop_start()
        except Exception as err:
            print(f"ERR ! /{err}/")

    def __del__(self):
        self.camera.close()

    def __iter__(self):
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

        with picamera.array.PiRGBArray(self.camera, size=self.size) as stream:
            while True:
                self.camera.capture(stream, format='bgr', resize=self.size, use_video_port=True)
                frame = stream.array
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(80, 80))

                text = "none" if len(faces) == 0 else "detected"
                cv2.putText(frame, text, (int(self.size[0] / 2.5), 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

                for (x, y, w, h) in faces:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 0, 255), 2)

                is_success, buffer = cv2.imencode(".jpg", frame, encode_param)
                image = buffer.tobytes()

                # 프레임 저장 (1초에 25개)
                self.frames_to_save.append(frame)

                yield (
                    b'--myboundary\n'
                    b'Content-Type: image/jpeg\n'
                    b'Content-Length: ' + str(len(buffer)).encode() + b'\n'
                    b'\n' + image + b'\n'
                )

                stream.truncate(0)

                if self.istilt:
                    self.tilt_on()

                print(len(self.frames_to_save))
                if len(self.frames_to_save) == 100:
                    self.save_frames_as_mp4()  # 250개의 프레임을 한 번에 .mp4 형식으로 저장
                    self.frames_to_save = []  # 저장한 프레임 리스트 초기화
                    self.cleanup_files()


    # frames_to_save 리스트에 있는 프레임을 mp4 형식으로 변환
    def save_frames_as_mp4(self):
        dtime = datetime.now().strftime('%y%m%d_%H%M%S')
        file_path = os.path.join(self.save_directory, "recorded{0}.mp4".format(dtime))
        writer = cv2.VideoWriter(file_path, cv2.VideoWriter_fourcc(*'mp4v'), self.framerate, self.size)

        for frame in self.frames_to_save:
            writer.write(frame)

        writer.release()
        
    # 녹화 파일 관리 메소드
    def cleanup_files(self):
        print("파일 정리 하실게요~")
        files = sorted(os.listdir(self.save_directory))
        num_files = len(files)

        if num_files > self.max_files: # media 폴더에 파일이 self.max_files개 보다 많다면 가장 오래된 파일 삭제
            files_to_delete = files[:num_files - self.max_files]
        
            for file_name in files_to_delete:
                file_path = os.path.join(self.save_directory, file_name)
                subprocess.run(["rm", file_path])

    # 충격 감지
    def tilt_on(self):
        print("@@@충격 감지@@@")
        # 현재 self.frames_to_save 리스트에 있는 프레임 개수 상관없이 즉시 mp4로 저장
        self.save_frames_as_mp4()
        self.frames_to_save=[]

        files = sorted(os.listdir(self.save_directory))
        # [::-2] 방금 저장한거 + 최근꺼 1개 업로드
        self.upload_file(files[-1])
        self.upload_file(files[-2])
        
        self.cleanup_files()

        self.istilt=False


    def upload_file(self, file_name):
        # 파일을 업로드할 url
        url = "http://127.0.0.1:8000/mjpeg/upload/"
        file_path = self.save_directory + '/' + file_name

        with open(file_path, 'rb') as f:
            data = {
                'file_name': file_name
            }
            files = {'sec_file': (file_name, f, 'video/mp4')}
            # files = {'sec_file': f}

            try:
                response = requests.post(url, data=data, files=files, timeout=10)
                response.raise_for_status()
                print(f"{file_path} 업로드 완료")
            except requests.exceptions.RequestException as e:
                print(f"{file_path} 업로드 실패: {e}")

    def on_connect(self, client, userdata, flags, rc):
        print("Connected with result code " + str(rc))
        if rc == 0:
            print("MQTT 연결 성공, rccar/response/# 구독 신청 . . @. @. . ")
            self.client.subscribe("rccar/response/#")
        else: 
            print("연결 실패 : ", rc)

    
    def on_message(self, client, userdata, msg):
        value = str(msg.payload.decode())
        _, _, router = msg.topic.split("/")

        if router=="tilt":
            print("기울었다고 전해라")
            self.istilt=True