# encoding=utf-8

import argparse
import os
import threading
import torch
from easydict import EasyDict as edict

import utils.torch_utils as torch_utils
from ByteTracker.byte_tracker import BYTETracker
from models import *  # set ONNX_EXPORT in models.py
from tracker.multitracker import JDETracker, MCJDETracker
from tracking_utils import visualization as vis
from tracking_utils.io import write_results_dict
from utils.datasets import *
from utils.utils import *


def format_output(dets, w, h):
    """
    :param dets: detection result input: x1, y1, x2, y2, score, cls_id
    :param w: image's original width
    :param h: image's original height
    :return: list of items: cls_id, conf_score, center_x, center_y,  bbox_w, bbox_h, [0, 1]
    """
    if dets is None:
        return None

    out_list = []

    for det in dets:
        x1, y1, x2, y2, score, cls_id = det
        center_x = (x1 + x2) * 0.5 / float(w)
        center_y = (y1 + y2) * 0.5 / float(h)
        bbox_w = (x2 - x1) / float(w)
        bbox_h = (y2 - y1) / float(h)
        out_list.append([int(cls_id), score, center_x, center_y, bbox_w, bbox_h])

    return out_list


def run_detection(opt):
    """
    :param opt:
    :return:
    """
    print('Start detection...')
    print('Net input size: {:d}×{:d}.'.format(opt.net_w, opt.net_h))

    # Set dataset and device
    if opt.input_type == 'videos':
        out_fps = int(float(opt.fps) / float(opt.interval) + 0.5)
        data_type = 'mot'
        video_path_list = [opt.videos + '/' + x for x in os.listdir(opt.videos) if x.endswith('.mp4')]
        video_path_list.sort()

        # # tracking each input video
        # for video_i, video_path in enumerate(video_path_list):
    elif opt.input_type == 'txt':
        if os.path.isfile(opt.source):
            print('Source test txt: {:s}.'.format(opt.source))
            with open(opt.source, 'r', encoding='utf-8') as r_h:
                paths = [x.strip() for x in r_h.readlines()]
                print('Total {:d} image files.'.format(len(paths)))
                dataset = LoadImages(paths, opt.img_proc_method, net_w=opt.net_w, net_h=opt.net_h)
        else:
            dataset = LoadImages(opt.source, opt.img_proc_method, net_w=opt.net_w, net_h=opt.net_h)

    if os.path.isdir(opt.output_txt_dir):
        shutil.rmtree(opt.output_txt_dir)
        os.makedirs(opt.output_txt_dir)
    else:
        os.makedirs(opt.output_txt_dir)

    # set device
    opt.device = str(find_free_gpu())
    print('Using gpu: {:s}'.format(opt.device))
    device = torch_utils.select_device(device='cpu' if ONNX_EXPORT else opt.device)
    opt.device = device

    # Set result output
    frame_dir = opt.save_img_dir + '/frame'
    if not os.path.isdir(frame_dir):
        os.makedirs(frame_dir)
    else:
        shutil.rmtree(frame_dir)
        os.makedirs(frame_dir)

    # class name to class id and class id to class name
    names = load_classes(opt.names)
    id2cls = defaultdict(str)
    cls2id = defaultdict(int)
    for cls_id, cls_name in enumerate(names):
        id2cls[cls_id] = cls_name
        cls2id[cls_name] = cls_id

    # Set MCMOT tracker
    tracker = MCJDETracker(opt)  # Multi-class joint detection & embedding

    if opt.input_type == 'txt':
        for fr_id, (path, img, img0, vid_cap) in enumerate(dataset):
            img = torch.from_numpy(img).to(opt.device)
            img = img.float()  # uint8 to fp32
            img /= 255.0  # 0 - 255 to 0.0 - 1.0

            ## ----- for debugging...
            # img.cpu().numpy().tofile('/mnt/diskb/even/input.bin')

            if img.ndimension() == 3:
                img = img.unsqueeze(0)

            t1 = torch_utils.time_synchronized()

            # ---------- update detection result of this frame
            dets = tracker.update_detection(img, img0)
            # ----------

            t2 = torch_utils.time_synchronized()
            print('%s done, time (%.3fs)' % (path, t2 - t1))

            if opt.show_image:
                online_im = vis.plot_detects(img=img0,
                                             dets=dets,
                                             num_classes=opt.num_classes,
                                             frame_id=fr_id,
                                             id2cls=id2cls)

            if opt.save_img_dir is not None:
                # save_img_path = os.path.join(frame_dir, '{:05d}.jpg'.format(fr_id))
                save_img_path = frame_dir + '/' + os.path.split(path)[-1]
                cv2.imwrite(save_img_path, online_im)
                print('{:s} saved.'.format(save_img_path))

            # output results as .txt file
            if dets is None:
                print('\n[Warning]: non objects detected in {}, frame id {:d}\n' \
                      .format(os.path.split(path), fr_id))
                dets_list = []
            else:
                dets_list = format_output(dets, w=img0.shape[1], h=img0.shape[0])

            # output label(txt) to disk
            out_img_name = os.path.split(path)[-1]
            out_f_name = out_img_name.replace('.jpg', '.txt')
            out_f_path = opt.output_txt_dir + '/' + out_f_name
            with open(out_f_path, 'w', encoding='utf-8') as w_h:
                w_h.write('class prob x y w h total=' + str(len(dets_list)) + '\n')  # write the first row
                for det in dets_list:
                    w_h.write('%d %f %f %f %f %f\n' % (det[0], det[1], det[2], det[3], det[4], det[5]))
            print('{} written'.format(out_f_path))

        print('Total {:d} images tested.'.format(fr_id + 1))

    elif opt.input_type == 'videos':
        # tracking each input video
        for video_i, video_path in enumerate(video_path_list):
            if video_i > 0:
                tracker.reset()

            # set dataset
            dataset = LoadImages(video_path, opt.img_proc_method, net_w=opt.net_w, net_h=opt.net_h)

            # set txt results path
            src_name = os.path.split(video_path)[-1]
            name, suffix = src_name.split('.')

            # set sampled frame count
            fr_cnt = 0

            # reset(clear) frame directory: write opt.save_img_dir
            shutil.rmtree(frame_dir)
            os.makedirs(frame_dir)

            # iterate tracking results of each frame
            for fr_id, (path, img, img0, vid_cap) in enumerate(dataset):
                # ----- img pre-processing
                img = torch.from_numpy(img).to(opt.device)
                img = img.float()  # uint8 to fp32
                img /= 255.0  # 0 - 255 to 0.0 - 1.0

                if img.ndimension() == 3:
                    img = img.unsqueeze(0)

                # ----- update tracking result of this frame
                if opt.interval == 1:

                    # ---------- update detection result of this frame
                    dets = tracker.update_detection(img, img0)
                    # ----------

                    if opt.show_image:
                        online_im = vis.plot_detects(img=img0,
                                                     dets=dets,
                                                     num_classes=opt.num_classes,
                                                     frame_id=fr_id,
                                                     id2cls=id2cls)

                    if opt.save_img_dir is not None:
                        save_img_path = os.path.join(frame_dir, '{:05d}.jpg'.format(fr_id))
                        cv2.imwrite(save_img_path, online_im)
                        print('{:s} saved.'.format(save_img_path))

                else:  # interval > 1
                    if fr_id % opt.interval == 0:  # skip some frames

                        # ---------- update detection result of this frame
                        dets = tracker.update_detection(img, img0)
                        # ----------

                        if opt.show_image:
                            online_im = vis.plot_detects(img=img0,
                                                         dets=dets,
                                                         num_classes=opt.num_classes,
                                                         frame_id=fr_cnt,
                                                         id2cls=id2cls)

                        if opt.save_img_dir is not None:
                            save_img_path = os.path.join(frame_dir, '{:05d}.jpg'.format(fr_cnt))
                            cv2.imwrite(save_img_path, online_im)
                            print('{:s} saved.'.format(save_img_path))

                        # update sampled frame count
                        fr_cnt += 1

            # output tracking result as video: read and write opt.save_img_dir
            result_video_path = opt.save_img_dir + '/' + name + '_detect' + '_fps' + str(out_fps) + '.' + suffix
            cmd_str = 'ffmpeg -f image2 -r {:d} -i {}/%05d.jpg -b 5000k -c:v mpeg4 {}' \
                .format(out_fps, frame_dir, result_video_path)
            os.system(cmd_str)


def track_videos_txt(opt):
    """
    :param opt:
    :return:
    """
    if not os.path.isdir(opt.videos):
        print('[Err]: invalid video directory.')
        return

    # set device
    opt.device = str(find_free_gpu())
    print('Using gpu: {:s}'.format(opt.device))
    device = torch_utils.select_device(device='cpu' if ONNX_EXPORT else opt.device)
    opt.device = device

    # set result output
    frame_dir = opt.save_img_dir + '/frame'
    if not os.path.isdir(frame_dir):
        os.makedirs(frame_dir)

    # class name to class id and class id to class name
    names = load_classes(opt.names)
    id2cls = defaultdict(str)
    cls2id = defaultdict(int)
    for cls_id, cls_name in enumerate(names):
        id2cls[cls_id] = cls_name
        cls2id[cls_name] = cls_id

    ## Set MCMOT tracker
    tracker = MCJDETracker(opt)  # Multi-class joint detection & embedding

    out_fps = int(opt.fps / opt.interval)
    data_type = 'mot'
    video_path_list = [opt.videos + '/' + x for x in os.listdir(opt.videos) if x.endswith('.mp4')]
    video_path_list.sort()
    print('Total {:d} videos for tracking.'.format(len(video_path_list)))

    # tracking each input video
    for video_i, video_path in enumerate(video_path_list):
        # set MCMOT tracker
        if video_i > 0:  # reset tracker for each in-dependant video
            tracker.reset()

        # set dataset
        dataset = LoadImages(video_path, opt.img_proc_method, net_w=opt.net_w, net_h=opt.net_h)

        # set txt results path
        src_name = os.path.split(video_path)[-1]
        name, suffix = src_name.split('.')
        result_f_name = opt.save_img_dir + '/' + name + '_results_fps{:d}.txt'.format(out_fps)

        # set dict to store tracking results for txt output
        res_dict = defaultdict(list)

        # set sampled frame count
        fr_cnt = 0

        # iterate tracking results of each frame
        for fr_id, (path, img, img0, vid_cap) in enumerate(dataset):
            img = torch.from_numpy(img).to(opt.device)
            img = img.float()  # uint8 to fp32
            img /= 255.0  # 0 - 255 to 0.0 - 1.0
            if img.ndimension() == 3:
                img = img.unsqueeze(0)

            # update tracking result of this frame
            if opt.interval == 1:
                # ---------- Update tracking results of current frame
                online_targets_dict = tracker.update_track_fair(img, img0)

                # ----------

                if online_targets_dict is None:
                    print('[Warning]: Skip frame {:d}.'.format(fr_id))
                    continue

                # aggregate current frame's results for each object class
                online_tlwhs_dict = defaultdict(list)
                online_ids_dict = defaultdict(list)

                # iterate each object class
                for cls_id in range(opt.num_classes):  # process each object class
                    online_targets = online_targets_dict[cls_id]
                    for track in online_targets:
                        online_tlwhs_dict[cls_id].append(track.tlwh)
                        online_ids_dict[cls_id].append(track.track_id)

                # collect result
                for cls_id in range(opt.num_classes):
                    res_dict[cls_id].append((fr_id + 1, online_tlwhs_dict[cls_id], online_ids_dict[cls_id]))
            else:
                if fr_id % opt.interval == 0:  # skip some frames
                    online_targets_dict = tracker.update_track_fair(img, img0)

                    if online_targets_dict is None:
                        print('[Warning]: Skip frame {:d}.'.format(fr_cnt))
                        continue

                    # aggregate current frame's results for each object class
                    online_tlwhs_dict = defaultdict(list)
                    online_ids_dict = defaultdict(list)

                    # iterate each object class
                    for cls_id in range(opt.num_classes):  # process each object class
                        online_targets = online_targets_dict[cls_id]
                        for track in online_targets:
                            online_tlwhs_dict[cls_id].append(track.tlwh)
                            online_ids_dict[cls_id].append(track.track_id)

                    # collect result
                    for cls_id in range(opt.num_classes):
                        res_dict[cls_id].append((fr_cnt + 1, online_tlwhs_dict[cls_id], online_ids_dict[cls_id]))

                    # update sampled frame count
                    fr_cnt += 1

        if opt.interval == 1:
            print('Total {:d} frames.'.format(fr_id + 1))
        else:
            print('Total {:d} frames.'.format(fr_cnt))

        # output track/detection results as txt(MOT16 format)
        write_results_dict(result_f_name, res_dict, data_type)  # write txt to opt.save_img_dir


def track_videos_vid(opt):
    """
    :param opt:
    :return:
    """
    if not os.path.isdir(opt.videos):
        print('[Err]: invalid video directory.')
        return

    # set device
    opt.device = str(find_free_gpu())
    print('Using gpu: {:s}'.format(opt.device))
    device = torch_utils.select_device(device='cpu' if ONNX_EXPORT else opt.device)
    opt.device = device

    # set result output
    frame_dir = opt.save_img_dir + '/tmp'
    if not os.path.isdir(frame_dir):
        os.makedirs(frame_dir)
    else:
        shutil.rmtree(frame_dir)
        os.makedirs(frame_dir)

    # class name to class id and class id to class name
    names = load_classes(opt.names)
    id2cls = defaultdict(str)
    cls2id = defaultdict(int)
    for cls_id, cls_name in enumerate(names):
        id2cls[cls_id] = cls_name
        cls2id[cls_name] = cls_id

    # Set MCMOT tracker: Multi-class joint detection & embedding
    tracker = MCJDETracker(opt)  #

    ## ---------- Set ByteTrack backend
    byte_args = {
        "mot20": False,
        "match_thresh": 0.8,  # 0.8
        "n_classes": 5,
        "track_buffer": 240,  # 30 | 60 | 90 | 120 | 150 | 180 | 210 | 240
        "track_thresh": 0.5  # 0.5
    }
    byte_args = edict(byte_args)
    tracker.backend = BYTETracker(byte_args, frame_rate=30)
    # print(tracker.backend)
    ## ----------

    out_fps = int(float(opt.fps) / float(opt.interval))
    data_type = 'mot'
    video_path_list = [opt.videos + '/' + x for x in os.listdir(opt.videos) if x.endswith('.mp4')]
    video_path_list.sort()

    # tracking each input video
    for video_i, video_path in enumerate(video_path_list):
        if video_i > 0:
            tracker.reset()
            if tracker.backend is not None:
                tracker.backend.reset()

        # set dataset
        dataset = LoadImages(video_path, opt.img_proc_method, net_w=opt.net_w, net_h=opt.net_h)

        # get video name
        src_name = os.path.split(video_path)[-1]
        vid_name, ext = src_name.split('.')

        # set sampled frame count
        fr_cnt = 0

        # reset(clear) frame directory: write opt.save_img_dir
        shutil.rmtree(frame_dir)
        os.makedirs(frame_dir)

        # iterate tracking results of each frame
        for fr_id, (path, img, img0, vid_cap) in enumerate(dataset):
            ## -----
            # img0: original image data(read from opencv and HWC)

            img = torch.from_numpy(img).to(opt.device)
            img = img.float()  # uint8 to fp32
            img /= 255.0  # 0 - 255 to 0.0 - 1.0
            if img.ndimension() == 3:
                img = img.unsqueeze(0)

            # Update tracking result of this frame
            if opt.interval == 1:
                if opt.name == "fair":
                    # ----- Update tracking result of current frame
                    online_targets_dict = tracker.update_track_fair(img, img0)
                    # -----

                elif opt.name == "byte":
                    # ----- Using ByteTrack backend
                    online_targets_dict = tracker.update_track_byte_emb(img, img0)
                    # -----

                if online_targets_dict is None:
                    print('[Warning]: Skip frame {:d}.'.format(fr_id))
                    continue

                # aggregate current frame's results for each object class
                online_tlwhs_dict = defaultdict(list)
                online_ids_dict = defaultdict(list)
                for cls_id in range(opt.num_classes):  # process each object class
                    online_targets = online_targets_dict[cls_id]
                    for track in online_targets:
                        online_tlwhs_dict[cls_id].append(track.tlwh)
                        online_ids_dict[cls_id].append(track.track_id)

                # to draw track/detection
                online_im = vis.plot_tracks(image=img0,
                                            tlwhs_dict=online_tlwhs_dict,
                                            obj_ids_dict=online_ids_dict,
                                            num_classes=opt.num_classes,
                                            frame_id=fr_id,
                                            id2cls=id2cls)

                if opt.save_img_dir is not None:
                    save_path = os.path.join(frame_dir, '{:05d}.jpg'.format(fr_id))
                    cv2.imwrite(save_path, online_im)
            else:  # interval > 1
                if fr_id % opt.interval == 0:  # skip some frames

                    # ----- update tracking result of current frame
                    online_targets_dict = tracker.update_track_fair(img, img0)
                    # -----

                    if online_targets_dict is None:
                        print('[Warning]: Skip frame {:d}.'.format(fr_cnt))
                        continue

                    # aggregate current frame's results for each object class
                    online_tlwhs_dict = defaultdict(list)
                    online_ids_dict = defaultdict(list)

                    # iterate each object class
                    for cls_id in range(opt.num_classes):  # process each object class
                        online_targets = online_targets_dict[cls_id]
                        for track in online_targets:
                            online_tlwhs_dict[cls_id].append(track.tlwh)
                            online_ids_dict[cls_id].append(track.track_id)

                    # to draw track/detection
                    online_im = vis.plot_tracks(image=img0,
                                                tlwhs_dict=online_tlwhs_dict,
                                                obj_ids_dict=online_ids_dict,
                                                num_classes=opt.num_classes,
                                                frame_id=fr_cnt,
                                                id2cls=id2cls)

                    if opt.save_img_dir is not None:
                        save_path = os.path.join(frame_dir, '{:05d}.jpg'.format(fr_cnt))
                        cv2.imwrite(save_path, online_im)  # write img to opt.save_img_dir

                    # update sampled frame count
                    fr_cnt += 1

        # output tracking result as video: read and write opt.save_img_dir
        print('Zip to mp4 in fps: {:d}'.format(out_fps))
        result_video_path = opt.save_img_dir + '/' + vid_name \
                            + '_track' + '_fps' + str(out_fps) + "_" + opt.name + '.' + ext
        cmd_str = 'ffmpeg -f image2 -r {:d} -i {}/%05d.jpg -b 5000k -c:v mpeg4 {}' \
            .format(out_fps, frame_dir, result_video_path)
        print(cmd_str)
        os.system(cmd_str)


class DemoRunner(object):
    def __init__(self):
        self.parser = argparse.ArgumentParser()

        self.parser.add_argument('--names',
                                 type=str,
                                 default='data/mcmot.names',
                                 help='*.names path')
        self.parser.add_argument("--name",
                                 type=str,
                                 default="byte",  # fair or byte
                                 help="")

        # ---------- cfg and weights file
        self.parser.add_argument('--cfg',
                                 type=str,
                                 default='cfg/yolov4_half_one_feat_fuse.cfg',
                                 help='*.cfg path')

        # yolov4-tiny-3l_no_group_id_SE_50000.weights or one_feat_fuse_track_last.weights
        self.parser.add_argument('--weights',
                                 type=str,
                                 default='weights/mcmot_half_track_last_211014.weights',
                                 help='weights path')
        # ----------

        # input file/folder, 0 for webcam
        self.parser.add_argument('--videos',
                                 type=str,
                                 default='/mnt/diskb/even/YOLOV4/data/videos',  # videos, test_videos
                                 help='')  # 'data/samples/videos/'
        self.parser.add_argument('--source',  # for detection
                                 type=str,
                                 default='./data/test2.txt',  # test1.txt or c5_test or test1.txt or test2.txt
                                 help='source')

        # output detection results as txt file for mMAP computation
        self.parser.add_argument('--output-txt-dir',
                                 type=str,
                                 default='/users/duanyou/c5/results_new/results_all/tmp.py')

        self.parser.add_argument('--save-img-dir',
                                 type=str,
                                 default='/mnt/diskb/even/YOLOV4/output',  # './results'
                                 help='dir to save visualized results(imgs).')

        # -----
        # task mode
        self.parser.add_argument('--task',
                                 type=str,
                                 default='track',  # track or detect
                                 help='task mode: track or detect')

        self.parser.add_argument('--input-type',
                                 type=str,
                                 default='videos',
                                 help='videos or txt')

        # output type
        self.parser.add_argument('--output-type',
                                 type=str,
                                 default='videos',
                                 help='videos or txts')
        # -----

        # output FPS interval
        self.parser.add_argument('--interval',
                                 type=int,
                                 default=1,
                                 help='The interval frame of tracking, default no interval.')

        # standard output FPS
        self.parser.add_argument('--fps',
                                 type=int,
                                 default=12,  # 12, 24
                                 help='The FPS of output video.')

        self.parser.add_argument('--output',
                                 type=str,
                                 default='output',
                                 help='output folder')  # output folder

        # ---------- Set net input image width and height
        self.parser.add_argument('--img-size',
                                 type=int,
                                 default=768,
                                 help='Image size')
        self.parser.add_argument('--net_w',
                                 type=int,
                                 default=768,
                                 help='inference size (pixels)')
        self.parser.add_argument('--net_h',
                                 type=int,
                                 default=448,
                                 help='inference size (pixels)')

        self.parser.add_argument('--num-classes',
                                 type=int,
                                 default=5,
                                 help='Number of object classes.')

        self.parser.add_argument('--track-buffer',
                                 type=int,
                                 default=30,  # 30, 60, 90, 120, 150, 180...
                                 help='tracking buffer frames')

        # ---------- NMS parameters: 0.3, 0.6 or 0.2, 0.45
        self.parser.add_argument('--conf-thres',
                                 type=float,
                                 default=0.2,
                                 help='object confidence threshold')
        self.parser.add_argument('--iou-thres',
                                 type=float,
                                 default=0.45,
                                 help='IOU threshold for NMS')
        # ----------

        # ---------- Input image Pre-processing method
        self.parser.add_argument('--img-proc-method',
                                 type=str,
                                 default='resize',
                                 help='Image pre-processing method(letterbox, resize)')

        # ----------

        self.parser.add_argument('--cutoff',
                                 type=int,
                                 default=0,  # 0 or 44, 47
                                 help='cutoff layer index, 0 means all layers loaded.')

        # ----- Set ReID feature map output layer ids
        self.parser.add_argument('--feat-out-ids',
                                 type=str,
                                 default='-1',  # '-5, -3, -1' or '-9, -5, -1' or '-1'
                                 help='reid feature map output layer ids.')

        self.parser.add_argument('--dim',
                                 type=int,
                                 default=128,  # 128, 256...
                                 help='reid feature map output embedding dimension')

        self.parser.add_argument('--fc',
                                 type=str,
                                 default='Arc',  # Arc
                                 help='FC layer type: FC or Arc')

        self.parser.add_argument('--device',
                                 default='7',
                                 help='device id (i.e. 0 or 0,1) or cpu')
        self.parser.add_argument('--view-img',
                                 action='store_true',
                                 help='display results')
        self.parser.add_argument('--save-txt',
                                 action='store_true',
                                 help='save results to *.txt')
        self.parser.add_argument('--classes',
                                 nargs='+',
                                 type=int,
                                 help='filter by class')
        self.parser.add_argument('--agnostic-nms',
                                 action='store_true',
                                 help='class-agnostic NMS')

        self.opt = self.parser.parse_args()
        print("Options:\n", self.opt)

    def run(self):
        """
        :return:
        """
        if self.opt.task == 'track':
            print('Run tracking...')
            if self.opt.output_type == 'txts':
                track_videos_txt(self.opt)
            elif self.opt.output_type == 'videos':
                track_videos_vid(self.opt)
            else:
                print('[Err]: un-recognized output mode.')
        elif self.opt.task == 'detect':
            print('Run detection...')
            run_detection(self.opt)
        else:
            print("[Err]: un-recognized task mode, neither 'track' or 'detect'")


if __name__ == '__main__':
    demo = DemoRunner()
    demo.run()
