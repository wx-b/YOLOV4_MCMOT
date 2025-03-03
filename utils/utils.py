# encoding=utf-8

import cv2
import glob
import math
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import numpy as np
import os
import random
import shutil
import subprocess
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
from pathlib import Path
from tqdm import tqdm

# import torch_utils as torch_utils  # , google_utils

# Set printoptions
torch.set_printoptions(linewidth=320, precision=5, profile='long')
np.set_printoptions(linewidth=320, formatter={'float_kind': '{:11.5g}'.format})  # format short g, %precision=5
matplotlib.rc('font', **{'size': 11})

# Prevent OpenCV from multithreading (to use PyTorch DataLoader)
cv2.setNumThreads(0)


def find_free_gpu():
    """
    :return:
    """
    os.system('nvidia-smi -q -d Memory |grep -A4 GPU|grep Free > tmp.py')
    memory_left_gpu = [int(x.split()[2]) for x in open('tmp.py', 'r').readlines()]

    most_free_gpu_idx = np.argmax(memory_left_gpu)
    # print(str(most_free_gpu_idx))
    return int(most_free_gpu_idx)


def cos(vect_1, vect_2):
    """
    :param vect_1:
    :param vect_2:
    :return:
    """
    norm1 = math.sqrt(sum(list(map(lambda x: math.pow(x, 2), vect_1))))
    norm2 = math.sqrt(sum(list(map(lambda x: math.pow(x, 2), vect_2))))
    return sum([vect_1[i] * vect_2[i] for i in range(0, len(vect_1))]) / (norm1 * norm2)


def euclidean(vect_1, vect_2):
    """
    :param vect_1:
    :param vect_2:
    :return:
    """
    return np.sqrt(np.sum((vect_1 - vect_2) ** 2))


def SSIM(vect_1, vect_2):
    """
    :param vect_1:
    :param vect_2:
    :return:
    """
    u_true = np.mean(vect_1)
    u_pred = np.mean(vect_2)
    var_true = np.var(vect_1)
    var_pred = np.var(vect_2)
    std_true = np.sqrt(var_true)
    std_pred = np.sqrt(var_pred)
    c1 = np.square(0.01 * 7)
    c2 = np.square(0.03 * 7)
    ssim = (2 * u_true * u_pred + c1) * (2 * std_pred * std_true + c2)
    denom = (u_true ** 2 + u_pred ** 2 + c1) * (var_pred + var_true + c2)
    return ssim / denom


def init_seeds(seed=0):
    """
    :param seed:
    :return:
    """
    random.seed(seed)
    np.random.seed(seed)
    torch_utils.init_seeds(seed=seed)


def check_git_status():
    """
    :return:
    """
    # Suggest 'git pull' if repo is out of date
    s = subprocess.check_output('if [ -d .git ]; then git fetch && git status -uno; fi', shell=True).decode('utf-8')
    if 'Your branch is behind' in s:
        print(s[s.find('Your branch is behind'):s.find('\n\n')] + '\n')


def load_classes(path):
    """
    :param path:
    :return:
    """
    # Loads *.names file at 'path'
    with open(path, 'r') as f:
        names = f.read().split('\n')
    return list(filter(None, names))  # filter removes empty strings (such as last line)


def labels_to_class_weights(labels, nc=80):
    """
    :param labels:
    :param nc:
    :return:
    """
    # Get class weights (inverse frequency) from training labels
    if labels[0] is None:  # no labels loaded
        return torch.Tensor()

    labels = np.concatenate(labels, 0)  # labels.shape = (866643, 5) for COCO
    classes = labels[:, 0].astype(np.int)  # labels = [class xywh]
    weights = np.bincount(classes, minlength=nc)  # occurences per class

    # Prepend gridpoint count (for uCE trianing)
    # gpi = ((320 / 32 * np.array([1, 2, 4])) ** 2 * 3).sum()  # gridpoints per image
    # weights = np.hstack([gpi * len(labels)  - weights.sum() * 9, weights * 9]) ** 0.5  # prepend gridpoints to start

    weights[weights == 0] = 1  # replace empty bins with 1
    weights = 1 / weights  # number of targets per class
    weights /= weights.sum()  # normalize
    return torch.from_numpy(weights)


def labels_to_image_weights(labels, nc=80, class_weights=np.ones(80)):
    """
    :param labels:
    :param nc:
    :param class_weights:
    :return:
    """
    # Produces image weights based on class mAPs
    n = len(labels)
    class_counts = np.array([np.bincount(labels[i][:, 0].astype(np.int), minlength=nc) for i in range(n)])
    image_weights = (class_weights.reshape(1, nc) * class_counts).sum(1)
    # index = random.choices(range(n), weights=image_weights, k=1)  # weight image sample
    return image_weights


def coco_class_weights():  # frequency of each class in coco train2014
    """
    :return:
    """
    n = [187437, 4955, 30920, 6033, 3838, 4332, 3160, 7051, 7677, 9167, 1316, 1372, 833, 6757, 7355, 3302, 3776, 4671,
         6769, 5706, 3908, 903, 3686, 3596, 6200, 7920, 8779, 4505, 4272, 1862, 4698, 1962, 4403, 6659, 2402, 2689,
         4012, 4175, 3411, 17048, 5637, 14553, 3923, 5539, 4289, 10084, 7018, 4314, 3099, 4638, 4939, 5543, 2038, 4004,
         5053, 4578, 27292, 4113, 5931, 2905, 11174, 2873, 4036, 3415, 1517, 4122, 1980, 4464, 1190, 2302, 156, 3933,
         1877, 17630, 4337, 4624, 1075, 3468, 135, 1380]
    weights = 1 / torch.Tensor(n)
    weights /= weights.sum()
    # with open('data/coco.names', 'r') as f:
    #     for k, v in zip(f.read().splitlines(), n):
    #         print('%20s: %g' % (k, v))
    return weights


def coco80_to_coco91_class():  # converts 80-index (val2014) to 91-index (paper)
    """
    :return:
    """
    # https://tech.amikelive.com/node-718/what-object-categories-labels-are-in-coco-dataset/
    # a = np.loadtxt('data/coco.names', dtype='str', delimiter='\n')
    # b = np.loadtxt('data/coco_paper.names', dtype='str', delimiter='\n')
    # x1 = [list(a[i] == b).index(True) + 1 for i in range(80)]  # darknet to coco
    # x2 = [list(b[i] == a).index(True) if any(b[i] == a) else None for i in range(91)]  # coco to darknet
    x = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 31, 32, 33, 34,
         35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63,
         64, 65, 67, 70, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 84, 85, 86, 87, 88, 89, 90]
    return x


def xyxy2xywh(x):
    """
    :param x:
    :return:
    """
    # Transform box coordinates from [x1, y1, x2, y2] (where xy1=top-left, xy2=bottom-right) to [x, y, w, h] 
    y = torch.zeros_like(x) if isinstance(x, torch.Tensor) else np.zeros_like(x)
    y[:, 0] = (x[:, 0] + x[:, 2]) / 2  # x center
    y[:, 1] = (x[:, 1] + x[:, 3]) / 2  # y center
    y[:, 2] = x[:, 2] - x[:, 0]  # width
    y[:, 3] = x[:, 3] - x[:, 1]  # height
    return y


def xywh2xyxy(x):
    """
    Transform box coordinates from [x, y, w, h] to [x1, y1, x2, y2] (where xy1=top-left, xy2=bottom-right)
    :param x:
    :return:
    """
    y = torch.zeros_like(x) if isinstance(x, torch.Tensor) else np.zeros_like(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2  # top left x: x_center - 0.5 * w
    y[:, 1] = x[:, 1] - x[:, 3] / 2  # top left y: y_center - 0.5 * h
    y[:, 2] = x[:, 0] + x[:, 2] / 2  # bottom right x: x_Center + 0.5 * w
    y[:, 3] = x[:, 1] + x[:, 3] / 2  # bottom right y: y_center + 0.5 * h
    return y


# def xywh2xyxy(box):
#     # Convert nx4 boxes from [x, y, w, h] to [x1, y1, x2, y2]
#     if isinstance(box, torch.Tensor):
#         x, y, w, h = box.t()
#         return torch.stack((x - w / 2, y - h / 2, x + w / 2, y + h / 2)).t()
#     else:  # numpy
#         x, y, w, h = box.T
#         return np.stack((x - w / 2, y - h / 2, x + w / 2, y + h / 2)).T
#
#
# def xyxy2xywh(box):
#     # Convert nx4 boxes from [x1, y1, x2, y2] to [x, y, w, h]
#     if isinstance(box, torch.Tensor):
#         x1, y1, x2, y2 = box.t()
#         return torch.stack(((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)).t()
#     else:  # numpy
#         x1, y1, x2, y2 = box.T
#         return np.stack(((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)).T

# coordinate transformation: convert back to original image coordinate
# for resizing pre-processing
def map_resize_back(dets, net_w, net_h, img_w, img_h):
    """
    :param dets:
    :param net_w:    eg: 768
    :param net_h:    eg: 448
    :param img_w:   eg: 1920
    :param img_h:   eg: 1080
    :return:
    """
    dets[:, 0] = dets[:, 0] / net_w * img_w  # x1
    dets[:, 2] = dets[:, 2] / net_w * img_w  # x2
    dets[:, 1] = dets[:, 1] / net_h * img_h  # y1
    dets[:, 3] = dets[:, 3] / net_h * img_h  # y2

    # clamp
    # clip_coords(dets[:, :4], (img_h, img_w))

    return dets


# 坐标系转换
def map_to_orig_coords(dets, net_w, net_h, orig_w, orig_h):
    """
    :param dets: x1, y1, x2, y2, score, class: n×6
    :param net_w:
    :param net_h:
    :param orig_w:
    :param orig_h:
    :return:
    """

    def get_padding():
        """
        :return:
        """
        ratio_x = float(net_w) / orig_w
        ratio_y = float(net_h) / orig_h
        ratio = min(ratio_x, ratio_y)

        # new_w, new_h
        new_shape = (round(orig_w * ratio), round(orig_h * ratio))
        new_w, new_h = new_shape

        pad_x = (net_w - new_w) * 0.5  # width padding
        pad_y = (net_h - new_h) * 0.5  # height padding

        left, right = round(pad_x - 0.1), round(pad_x + 0.1)
        top, bottom = round(pad_y - 0.1), round(pad_y + 0.1)

        return top, bottom, left, right, new_shape

    # pad_tl, pad_rb, pad_type, new_shape = get_padding()
    top, bottom, left, right, new_shape = get_padding()
    new_w, new_h = new_shape

    dets[:, 0] = (dets[:, 0] - left) / new_w * orig_w  # x1
    dets[:, 2] = (dets[:, 2] - left) / new_w * orig_w  # x2
    dets[:, 1] = (dets[:, 1] - top) / new_h * orig_h  # y1
    dets[:, 3] = (dets[:, 3] - top) / new_h * orig_h  # y2

    # clamp
    clip_coords(dets[:, :4], (orig_h, orig_w))

    return dets


# 坐标系转换
def scale_coords(img1_shape, coords, img0_shape, ratio_pad=None):
    """
    :param img1_shape:
    :param coords:
    :param img0_shape:
    :param ratio_pad:
    :return:
    """
    # Rescale coords (xyxy) from img1_shape to img0_shape
    if ratio_pad is None:  # calculate from img0_shape
        gain = max(img1_shape) / max(img0_shape)  # gain  = old / new
        pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
    else:
        gain = ratio_pad[0][0]
        pad = ratio_pad[1]

    coords[:, [0, 2]] -= pad[0]  # x padding
    coords[:, [1, 3]] -= pad[1]  # y padding
    coords[:, :4] /= gain  # scale back to img0's scale

    clip_coords(coords, img0_shape)

    return coords


def clip_coords(boxes, img_shape):
    """
    :param boxes:
    :param img_shape: h, w
    :return:
    """
    # Clip bounding xyxy bounding boxes to image shape (height, width)
    img_h, img_w = img_shape

    boxes[:, 0].clamp_(0, img_w - 1)  # x1
    boxes[:, 1].clamp_(0, img_h - 1)  # y1
    boxes[:, 2].clamp_(0, img_w - 1)  # x2
    boxes[:, 3].clamp_(0, img_h - 1)  # y2

    # boxes[:, 0] = np.clip(boxes[:, 0], 0, img_w - 1)  # x1
    # boxes[:, 1] = np.clip(boxes[:, 1], 0, img_h - 1)  # y1
    # boxes[:, 2] = np.clip(boxes[:, 2], 0, img_w - 1)  # x2
    # boxes[:, 3] = np.clip(boxes[:, 3], 0, img_h - 1)  # y2


def ap_per_class(tp, conf, pred_cls, target_cls):
    """ Compute the average precision, given the recall and precision curves.
    Source: https://github.com/rafaelpadilla/Object-Detection-Metrics.
    # Arguments
        tp:    True positives (nparray, nx1 or nx10).
        conf:  Objectness value from 0-1 (nparray).
        pred_cls: Predicted object classes (nparray).
        target_cls: True object classes (nparray).
    # Returns
        The average precision as computed in py-faster-rcnn.
    """

    # Sort by objectness
    i = np.argsort(-conf)
    tp, conf, pred_cls = tp[i], conf[i], pred_cls[i]

    # Find unique classes
    unique_classes = np.unique(target_cls)

    # Create Precision-Recall curve and compute AP for each class
    pr_score = 0.1  # score to evaluate P and R https://github.com/ultralytics/yolov3/issues/898
    s = [len(unique_classes), tp.shape[1]]  # number class, number iou thresholds (i.e. 10 for mAP0.5...0.95)
    ap, p, r = np.zeros(s), np.zeros(s), np.zeros(s)
    for ci, c in enumerate(unique_classes):
        i = pred_cls == c
        n_gt = (target_cls == c).sum()  # Number of ground truth objects
        n_p = i.sum()  # Number of predicted objects

        if n_p == 0 or n_gt == 0:
            continue
        else:
            # Accumulate FPs and TPs
            fpc = (1 - tp[i]).cumsum(0)
            tpc = tp[i].cumsum(0)

            # Recall
            recall = tpc / (n_gt + 1e-16)  # recall curve
            r[ci] = np.interp(-pr_score, -conf[i], recall[:, 0])  # r at pr_score, negative x, xp because xp decreases

            # Precision
            precision = tpc / (tpc + fpc)  # precision curve
            p[ci] = np.interp(-pr_score, -conf[i], precision[:, 0])  # p at pr_score

            # AP from recall-precision curve
            for j in range(tp.shape[1]):
                ap[ci, j] = compute_ap(recall[:, j], precision[:, j])

            # Plot
            # fig, ax = plt.subplots(1, 1, figsize=(5, 5))
            # ax.plot(recall, precision)
            # ax.set_xlabel('Recall')
            # ax.set_ylabel('Precision')
            # ax.set_xlim(0, 1.01)
            # ax.set_ylim(0, 1.01)
            # fig.tight_layout()
            # fig.savefig('PR_curve.png', dpi=300)

    # Compute F1 score (harmonic mean of precision and recall)
    f1 = 2 * p * r / (p + r + 1e-16)

    return p, r, ap, f1, unique_classes.astype('int32')


def compute_ap(recall, precision):
    """ Compute the average precision, given the recall and precision curves.
    Source: https://github.com/rbgirshick/py-faster-rcnn.
    # Arguments
        recall:    The recall curve (list).
        precision: The precision curve (list).
    # Returns
        The average precision as computed in py-faster-rcnn.
    """

    # Append sentinel values to beginning and end
    m_rec = np.concatenate(([0.], recall, [min(recall[-1] + 1E-3, 1.)]))
    m_pre = np.concatenate(([0.], precision, [0.]))

    # Compute the precision envelope
    m_pre = np.flip(np.maximum.accumulate(np.flip(m_pre)))

    # Integrate area under curve
    method = 'interp'  # methods: 'continuous', 'interp'
    if method == 'interp':
        x = np.linspace(0, 1, 101)  # 101-point interp (COCO)
        ap = np.trapz(np.interp(x, m_rec, m_pre), x)  # integrate
    else:  # 'continuous'
        i = np.where(m_rec[1:] != m_rec[:-1])[0]  # points where x axis (recall) changes
        ap = np.sum((m_rec[i + 1] - m_rec[i]) * m_pre[i + 1])  # area under curve

    return ap


def bbox_iou(box1, box2, x1y1x2y2=True, GIoU=False, DIoU=False, CIoU=False):
    """
    :param box1:
    :param box2:
    :param x1y1x2y2:
    :param GIoU:
    :param DIoU:
    :param CIoU:
    :return:
    """
    # Returns the IoU of box1 to box2. box1 is 4, box2 is nx4
    box2 = box2.t()

    # Get the coordinates of bounding boxes
    if x1y1x2y2:  # x1, y1, x2, y2 = box1
        b1_x1, b1_y1, b1_x2, b1_y2 = box1[0], box1[1], box1[2], box1[3]
        b2_x1, b2_y1, b2_x2, b2_y2 = box2[0], box2[1], box2[2], box2[3]
    else:  # transform from xywh to xyxy
        b1_x1, b1_x2 = box1[0] - box1[2] / 2, box1[0] + box1[2] / 2
        b1_y1, b1_y2 = box1[1] - box1[3] / 2, box1[1] + box1[3] / 2
        b2_x1, b2_x2 = box2[0] - box2[2] / 2, box2[0] + box2[2] / 2
        b2_y1, b2_y2 = box2[1] - box2[3] / 2, box2[1] + box2[3] / 2

    # Intersection area
    inter = (torch.min(b1_x2, b2_x2) - torch.max(b1_x1, b2_x1)).clamp(0) * \
            (torch.min(b1_y2, b2_y2) - torch.max(b1_y1, b2_y1)).clamp(0)

    # Union Area
    w1, h1 = b1_x2 - b1_x1, b1_y2 - b1_y1
    w2, h2 = b2_x2 - b2_x1, b2_y2 - b2_y1
    union = (w1 * h1 + 1e-16) + w2 * h2 - inter

    iou = inter / union  # iou
    if GIoU or DIoU or CIoU:
        cw = torch.max(b1_x2, b2_x2) - torch.min(b1_x1, b2_x1)  # convex (smallest enclosing box) width
        ch = torch.max(b1_y2, b2_y2) - torch.min(b1_y1, b2_y1)  # convex height
        if GIoU:  # Generalized IoU https://arxiv.org/pdf/1902.09630.pdf
            c_area = cw * ch + 1e-16  # convex area
            return iou - (c_area - union) / c_area  # GIoU
        if DIoU or CIoU:  # Distance or Complete IoU https://arxiv.org/abs/1911.08287v1
            # convex diagonal squared
            c2 = cw ** 2 + ch ** 2 + 1e-16
            # centerpoint distance squared
            rho2 = ((b2_x1 + b2_x2) - (b1_x1 + b1_x2)) ** 2 / 4 + ((b2_y1 + b2_y2) - (b1_y1 + b1_y2)) ** 2 / 4
            if DIoU:
                return iou - rho2 / c2  # DIoU
            elif CIoU:  # https://github.com/Zzh-tju/DIoU-SSD-pytorch/blob/master/utils/box/box_utils.py#L47
                v = (4 / math.pi ** 2) * torch.pow(torch.atan(w2 / h2) - torch.atan(w1 / h1), 2)
                with torch.no_grad():
                    alpha = v / (1 - iou + v)
                return iou - (rho2 / c2 + v * alpha)  # CIoU

    return iou


def box_iou(box1, box2):
    """
    :param box1:
    :param box2:
    :return:
    """
    # https://github.com/pytorch/vision/blob/master/torchvision/ops/boxes.py
    """
    Return intersection-over-union (Jaccard index) of boxes.
    Both sets of boxes are expected to be in (x1, y1, x2, y2) format.
    Arguments:
        box1 (Tensor[N, 4])
        box2 (Tensor[M, 4])
    Returns:
        iou (Tensor[N, M]): the NxM matrix containing the pairwise
            IoU values for every element in boxes1 and boxes2
    """

    def box_area(box):
        # box = 4xn
        return (box[2] - box[0]) * (box[3] - box[1])

    area1 = box_area(box1.t())
    area2 = box_area(box2.t())

    # inter(N, M) = (rb(N, M, 2) - lt(N, M, 2)).clamp(0).prod(2)
    inter = (torch.min(box1[:, None, 2:], box2[:, 2:]) - torch.max(box1[:, None, :2], box2[:, :2])).clamp(0).prod(2)
    return inter / (area1[:, None] + area2 - inter)  # iou = inter / (area1 + area2 - inter)


def box_iou_np(box1, box2):
    """
    向量化IOU计算: 利用numpy/pytorch的广播机制, 使用None扩展维度
    :param box1: (n, 4)
    :param box2: (m, 4)
    :return: (n, m)
    numpy 广播机制 从后向前对齐。 维度为1 的可以重复等价为任意维度
    eg: (4,3,2)   (3,2)  (3,2)会扩充为(4,3,2)
        (4,1,2)   (3,2) (4,1,2) 扩充为(4, 3, 2)  (3, 2)扩充为(4, 3,2) 扩充的方法为重复
    广播会在numpy的函数 如sum, maximun等函数中进行
    pytorch同理。
    扩充维度的方法：
    eg: a  a.shape: (3,2)  a[:, None, :] a.shape: (3, 1, 2) None 对应的维度相当于newaxis
    """
    lt = np.maximum(box1[:, None, :2], box2[:, :2])  # left_top (x, y)
    rb = np.minimum(box1[:, None, 2:], box2[:, 2:])  # right_bottom (x, y)
    wh = np.maximum(rb - lt + 1, 0)  # inter_area (w, h)
    inter = wh[:, :, 0] * wh[:, :, 1]  # shape: (n, m)
    box1_area = (box1[:, 2] - box1[:, 0] + 1) * (box1[:, 3] - box1[:, 1] + 1)
    box2_area = (box2[:, 2] - box2[:, 0] + 1) * (box2[:, 3] - box2[:, 1] + 1)
    iou_matrix = inter / (box1_area[:, None] + box2_area - inter + 1e-6)

    return iou_matrix


def box_ioa_np(box1, box2):
    """
    intersection over area
    :param box1:
    :param box2:
    :return:
    """
    lt = np.maximum(box1[:, None, :2], box2[:, :2])  # left_top (x, y)
    rb = np.minimum(box1[:, None, 2:], box2[:, 2:])  # right_bottom (x, y)
    wh = np.maximum(rb - lt + 1, 0)  # inter_area (w, h)
    inter = wh[:, :, 0] * wh[:, :, 1]  # shape: (n, m)
    box1_area = (box1[:, 2] - box1[:, 0] + 1) * (box1[:, 3] - box1[:, 1] + 1)
    box2_area = (box2[:, 2] - box2[:, 0] + 1) * (box2[:, 3] - box2[:, 1] + 1)
    iou_matrix = inter / (box1_area[:, None] + 1e-6)

    return iou_matrix


def wh_iou(wh1, wh2):
    """
    Using tensor's broadcasting mechanism
    :param wh1:
    :param wh2:
    :return: N×M matrix for each N×M's iou
    """
    # Returns the nxm IoU matrix. wh1 is nx2, wh2 is mx2
    wh1 = wh1[:, None]  # [N, 1, 2]
    wh2 = wh2[None]  # [1, M, 2]
    min_wh = torch.min(wh1, wh2)  # min w and min h for N and M box: N×M×2
    inter = min_wh.prod(dim=2)  # min_w × min_h for [N, M]
    return inter / (wh1.prod(2) + wh2.prod(2) - inter)  # iou = inter / (area1 + area2 - inter)


class FocalLoss(nn.Module):
    # Wraps focal loss_funcs around existing loss_fcn(), i.e. criteria = FocalLoss(nn.BCEWithLogitsLoss(), gamma=1.5)
    def __init__(self, loss_fcn, gamma=1.5, alpha=0.25):
        super(FocalLoss, self).__init__()
        self.loss_fcn = loss_fcn  # must be nn.BCEWithLogitsLoss()
        self.gamma = gamma
        self.alpha = alpha
        self.reduction = loss_fcn.reduction
        self.loss_fcn.reduction = 'none'  # required to apply FL to each element

    def forward(self, pred, true):
        loss = self.loss_fcn(pred, true)
        # p_t = torch.exp(-loss_funcs)
        # loss_funcs *= self.alpha * (1.000001 - p_t) ** self.gamma  # non-zero power for gradient stability

        # TF implementation https://github.com/tensorflow/addons/blob/v0.7.1/tensorflow_addons/losses/focal_loss.py
        pred_prob = torch.sigmoid(pred)  # prob from logits
        p_t = true * pred_prob + (1 - true) * (1 - pred_prob)
        alpha_factor = true * self.alpha + (1 - true) * (1 - self.alpha)
        modulating_factor = (1.0 - p_t) ** self.gamma
        loss *= alpha_factor * modulating_factor

        if self.reduction == 'mean':
            return loss.mean()
        elif self.reduction == 'sum':
            return loss.sum()
        else:  # 'none'
            return loss


def smooth_BCE(eps=0.1):  # https://github.com/ultralytics/yolov3/issues/238#issuecomment-598028441
    """
    :param eps:
    :return:
    """
    # return positive, negative label smoothing BCE targets
    return 1.0 - 0.5 * eps, 0.5 * eps


def compute_loss_one_layer(preds, reid_feat_out,
                           targets, track_ids,
                           model, dev,
                           s_id=-1.05, s_det=-1.85):
    """
    :param preds:
    :param reid_feat_out:
    :param targets:
    :param track_ids:
    :param model:
    :return:
    """
    s_id = nn.Parameter(s_id * torch.ones(1)).to(dev)  # -1.05
    s_det = nn.Parameter(s_det * torch.ones(1)).to(dev)  # -1.85

    ft = torch.cuda.FloatTensor if preds[0].is_cuda else torch.Tensor
    l_cls, l_box, l_obj, l_reid = ft([0]), ft([0]), ft([0]), ft([0])

    # build targets for loss_funcs computation
    t_cls, t_box, indices, anchor_vec, t_track_ids = build_targets_with_ids(preds, targets, track_ids, model)

    h = model.hyp  # hyper parameters
    reduction = 'mean'  # Loss reduction (sum or mean)

    # Define criteria
    BCE_cls = nn.BCEWithLogitsLoss(pos_weight=ft([h['cls_pw']]), reduction=reduction)
    BCE_obj = nn.BCEWithLogitsLoss(pos_weight=ft([h['obj_pw']]), reduction=reduction)
    CE_reid = nn.CrossEntropyLoss()
    ghm_c = GHMC(bins=100)  # 30, 60, 80, 100

    # class label smoothing https://arxiv.org/pdf/1902.04103.pdf eqn 3
    cp, cn = smooth_BCE(eps=0.0)

    # focal loss_funcs
    g = h['fl_gamma']  # focal loss_funcs gamma
    if g > 0:
        BCE_cls, BCE_obj = FocalLoss(BCE_cls, g), FocalLoss(BCE_obj, g)

    np, ng = 0, 0  # number grid points, targets(GT)

    # Compute losses for each YOLO layer(3 or 2 yolo layers)
    reid_feat_map = reid_feat_out[0]
    for i, pred_i in enumerate(preds):  # layer index, layer predictions
        id_map_w, id_map_h = reid_feat_map.shape[3], reid_feat_map.shape[2]  # only one feature map layer

        ny, nx = pred_i.shape[2], pred_i.shape[3]
        b, a, gy, gx = indices[i]  # image, anchor, grid_y, grid_x
        tr_ids = t_track_ids[i]  # track ids
        cls_ids = t_cls[i]

        t_obj = torch.zeros_like(pred_i[..., 0])  # target obj(confidence score), e.g. 5×3×96×96
        np += t_obj.numel()  # total number of elements

        # Compute losses
        nb = len(b)  # number of targets(GT boxes)
        if nb:  # if exist GT box
            ng += nb

            # prediction subset corresponding to targets
            # specified item_i_in_batch, anchor_i, grid_y, grid_x
            pred_s = pred_i[b, a, gy, gx]  # nb × 10
            # pred_s[:, 2:4] = torch.sigmoid(pred_s[:, 2:4])  # wh power loss_funcs (uncomment)

            # GIoU
            pxy = torch.sigmoid(pred_s[:, 0:2])  # pxy = pxy * s - (s - 1) / 2,  s = 1.5  (scale_xy)
            pwh = torch.exp(pred_s[:, 2:4]).clamp(max=1E3) * anchor_vec[i]
            p_box = torch.cat((pxy, pwh), 1)  # predicted bounding box
            g_iou = bbox_iou(p_box.t(), t_box[i], x1y1x2y2=False, GIoU=True)  # g_iou computation: in YOLO layer's scale
            l_box += (1.0 - g_iou).sum() if reduction == 'sum' else (1.0 - g_iou).mean()  # g_iou loss_funcs
            t_obj[b, a, gy, gx] = (1.0 - model.gr) + model.gr * g_iou.detach().clamp(0).type(
                t_obj.dtype)  # g_iou ratio taken into account

            if model.nc > 1:  # cls loss_funcs (only if multiple classes)
                t = torch.full_like(pred_s[:, 5:], cn)  # targets: nb × num_classes
                t[range(nb), cls_ids] = cp
                l_cls += BCE_cls(pred_s[:, 5:], t)  # BCE loss for each object class
                # l_cls += CE(pred_s[:, 5:], cls_ids)  # CE

            # ----- compute reid loss_funcs for each GT box
            # get center point coordinates for all GT
            center_x = gx + pred_s[:, 0]
            center_y = gy + pred_s[:, 1]

            # convert to reid_feature map's scale
            center_x *= float(id_map_w) / float(nx)
            center_y *= float(id_map_h) / float(ny)

            # convert to int64 for indexing
            center_x += 0.5
            center_y += 0.5
            center_x = center_x.long()
            center_y = center_y.long()

            # avoid exceed reid feature map's range
            center_x.clamp_(0, id_map_w - 1)
            center_y.clamp_(0, id_map_h - 1)

            # get reid feature vector for GT boxes
            t_reid_feat_vects = reid_feat_map[b, :, center_y, center_x]  # nb × 128: only one feature map layer

            # ----- compute each object class's reid loss_funcs
            multi_gpu = type(model) in (nn.parallel.DataParallel, nn.parallel.DistributedDataParallel)
            if multi_gpu:
                for cls_id, id_num in model.module.max_id_dict.items():
                    inds = torch.where(cls_ids == cls_id)
                    if inds[0].shape[0] == 0:
                        # print('skip class id', cls_id)
                        continue

                    id_vects = t_reid_feat_vects[inds]
                    id_vects = F.normalize(id_vects, dim=1)  # L2 normalize the feature vector

                    fc_preds = model.module.id_classifiers[cls_id].forward(id_vects).contiguous()
                    l_reid += CE_reid(fc_preds, tr_ids[inds])
            else:
                for cls_id, id_num in model.max_id_dict.items():
                    inds = torch.where(cls_ids == cls_id)
                    if inds[0].shape[0] == 0:
                        # print('skip class id', cls_id)
                        continue

                    id_vects = t_reid_feat_vects[inds]

                    # L2 normalize the feature vector
                    id_vects = F.normalize(id_vects, dim=1)

                    if model.fc_type == 'FC':
                        ## normal FC layer as classifier
                        fc_preds = model.id_classifiers[cls_id].forward(id_vects).contiguous()
                        # l_reid += CE_reid(fc_preds, tr_ids[inds])  # using cross entropy loss

                        ## using GHM-C loss for reid classification
                        target = torch.zeros_like(fc_preds)
                        target.scatter_(1, tr_ids[inds].view(-1, 1).long(), 1)
                        label_weight = torch.ones_like(fc_preds)
                        l_reid += ghm_c.forward(fc_preds, target, label_weight)

                    elif model.fc_type == 'Arc':
                        ## arc margin FC layer as classifier
                        fc_preds = model.id_classifiers[cls_id].forward(id_vects, tr_ids[inds]).contiguous()
                        # l_reid += CE_reid(fc_preds, tr_ids[inds])

                        ## using GHM-C loss for reid classification
                        target = torch.zeros_like(fc_preds)
                        target.scatter_(1, tr_ids[inds].view(-1, 1).long(), 1)
                        label_weight = torch.ones_like(fc_preds)
                        l_reid += ghm_c.forward(fc_preds, target, label_weight)

            # Append targets to text file
            # with open('targets.txt', 'a') as file:
            #     [file.write('%11.5g ' * 4 % tuple(x) + '\n') for x in torch.cat((txy[i], twh[i]), 1)]

        l_obj += BCE_obj(pred_i[..., 4], t_obj)  # obj loss_funcs(confidence score loss_funcs)

    l_box *= h['giou']
    l_obj *= h['obj']
    l_cls *= h['cls']
    # l_reid *= h['reid']
    # l_reid /= float(nb)  # reid loss_funcs normalize by number of GT objects

    if reduction == 'sum':
        bs = t_obj.shape[0]  # batch size
        l_obj *= 3 / (6300 * bs) * 2  # 3 / np * 2
        if ng:
            l_cls *= 3 / ng / model.nc
            l_box *= 3 / ng

    l_det = l_box + l_obj + l_cls
    # loss = l_det + l_reid
    loss = torch.exp(-s_det) * l_det \
           + torch.exp(-s_id) * l_reid \
           + (s_det + s_id)
    return loss, torch.cat((l_box, l_obj, l_cls, l_reid, loss)).detach()


def compute_loss_no_upsample(preds, reid_feat_out, targets, track_ids, model):
    """
    :param preds:
    :param reid_feat_out:
    :param targets:
    :param track_ids:
    :param model:
    :return:
    """
    ft = torch.cuda.FloatTensor if preds[0].is_cuda else torch.Tensor
    l_cls, l_box, l_obj, l_reid = ft([0]), ft([0]), ft([0]), ft([0])

    # build targets for loss_funcs computation
    t_cls, t_box, indices, anchor_vec, t_track_ids = build_targets_with_ids(preds, targets, track_ids, model)

    h = model.hyp  # hyper parameters
    red = 'mean'  # Loss reduction (sum or mean)

    # Define criteria
    BCE_cls = nn.BCEWithLogitsLoss(pos_weight=ft([h['cls_pw']]), reduction=red)
    BCE_obj = nn.BCEWithLogitsLoss(pos_weight=ft([h['obj_pw']]), reduction=red)
    CE_reid = nn.CrossEntropyLoss()

    # class label smoothing https://arxiv.org/pdf/1902.04103.pdf eqn 3
    cp, cn = smooth_BCE(eps=0.0)

    # focal loss_funcs
    g = h['fl_gamma']  # focal loss_funcs gamma
    if g > 0:
        BCE_cls, BCE_obj = FocalLoss(BCE_cls, g), FocalLoss(BCE_obj, g)

    np, ng = 0, 0  # number grid points, targets(GT)

    # Compute losses for each YOLO layer(3 or 2 yolo layers)
    for i, pred_i in enumerate(preds):  # layer index, layer predictions
        id_map_w, id_map_h = reid_feat_out[i].shape[3], reid_feat_out[i].shape[2]  # 3(or 2) feature map layers

        ny, nx = pred_i.shape[2], pred_i.shape[3]
        b, a, gy, gx = indices[i]  # image, anchor, grid_y, grid_x
        tr_ids = t_track_ids[i]  # track ids
        cls_ids = t_cls[i]

        t_obj = torch.zeros_like(pred_i[..., 0])  # target obj(confidence score), e.g. 5×3×96×96
        np += t_obj.numel()  # total number of elements

        # Compute losses
        nb = len(b)  # number of targets(GT boxes)
        if nb:  # if exist GT box
            ng += nb

            # prediction subset corresponding to targets
            # specified item_i_in_batch, anchor_i, grid_y, grid_x
            pred_s = pred_i[b, a, gy, gx]  # nb × 10
            # pred_s[:, 2:4] = torch.sigmoid(pred_s[:, 2:4])  # wh power loss_funcs (uncomment)

            # GIoU
            pxy = torch.sigmoid(pred_s[:, 0:2])  # pxy = pxy * s - (s - 1) / 2,  s = 1.5  (scale_xy)
            pwh = torch.exp(pred_s[:, 2:4]).clamp(max=1E3) * anchor_vec[i]
            p_box = torch.cat((pxy, pwh), 1)  # predicted bounding box
            g_iou = bbox_iou(p_box.t(), t_box[i], x1y1x2y2=False, GIoU=True)  # g_iou computation: in YOLO layer's scale
            l_box += (1.0 - g_iou).sum() if red == 'sum' else (1.0 - g_iou).mean()  # g_iou loss_funcs
            t_obj[b, a, gy, gx] = (1.0 - model.gr) + model.gr * g_iou.detach().clamp(0).type(
                t_obj.dtype)  # g_iou ratio taken into account

            if model.nc > 1:  # cls loss_funcs (only if multiple classes)
                t = torch.full_like(pred_s[:, 5:], cn)  # targets: nb × num_classes
                t[range(nb), cls_ids] = cp
                l_cls += BCE_cls(pred_s[:, 5:], t)  # BCE loss for each object class
                # l_cls += CE(pred_s[:, 5:], cls_ids)  # CE

            # ----- compute reid loss_funcs for each GT box
            # get center point coordinates for all GT
            center_x = gx + pred_s[:, 0]
            center_y = gy + pred_s[:, 1]

            # convert to reid_feature map's scale
            center_x *= float(id_map_w) / float(nx)
            center_y *= float(id_map_h) / float(ny)

            # convert to int64 for indexing
            center_x += 0.5
            center_y += 0.5
            center_x = center_x.long()
            center_y = center_y.long()

            # avoid exceed reid feature map's range
            center_x.clamp_(0, id_map_w - 1)
            center_y.clamp_(0, id_map_h - 1)

            # get reid feature vector for GT boxes
            t_reid_feat_vects = reid_feat_out[i][b, :, center_y, center_x]  # nb × 128

            # ----- compute each object class's reid loss_funcs
            multi_gpu = type(model) in (nn.parallel.DataParallel, nn.parallel.DistributedDataParallel)
            if multi_gpu:
                for cls_id, id_num in model.module.max_id_dict.items():
                    inds = torch.where(cls_ids == cls_id)
                    if inds[0].shape[0] == 0:
                        # print('skip class id', cls_id)
                        continue

                    id_vects = t_reid_feat_vects[inds]
                    id_vects = F.normalize(id_vects, dim=1)  # L2 normalize the feature vector

                    fc_preds = model.module.id_classifiers[cls_id].forward(id_vects).contiguous()
                    l_reid += CE_reid(fc_preds, tr_ids[inds])
            else:
                for cls_id, id_num in model.max_id_dict.items():
                    inds = torch.where(cls_ids == cls_id)
                    if inds[0].shape[0] == 0:
                        # print('skip class id', cls_id)
                        continue

                    id_vects = t_reid_feat_vects[inds]

                    # L2 normalize the feature vector
                    id_vects = F.normalize(id_vects, dim=1)

                    # # normal FC layer as classifier
                    fc_preds = model.id_classifiers[cls_id].forward(id_vects).contiguous()
                    l_reid += CE_reid(fc_preds, tr_ids[inds])

                    # arc margin FC layer as classifier
                    # fc_preds = model.id_classifiers[cls_id].forward(id_vects, tr_ids[inds]).contiguous()
                    # l_reid += CE_reid(fc_preds, tr_ids[inds])

            # Append targets to text file
            # with open('targets.txt', 'a') as file:
            #     [file.write('%11.5g ' * 4 % tuple(x) + '\n') for x in torch.cat((txy[i], twh[i]), 1)]

        l_obj += BCE_obj(pred_i[..., 4], t_obj)  # obj loss_funcs(confidence score loss_funcs)

    l_box *= h['giou']
    l_obj *= h['obj']
    l_cls *= h['cls']
    # l_reid *= h['reid']
    l_reid /= float(nb)  # reid loss_funcs normalize by number of GT objects

    if red == 'sum':
        bs = t_obj.shape[0]  # batch size
        l_obj *= 3 / (6300 * bs) * 2  # 3 / np * 2
        if ng:
            l_cls *= 3 / ng / model.nc
            l_box *= 3 / ng

    loss = l_box + l_obj + l_cls + l_reid
    return loss, torch.cat((l_box, l_obj, l_cls, l_reid, loss)).detach()


def compute_loss_with_ids(preds, reid_feat_out, targets, track_ids, model):
    """
    :param preds:
    :param reid_feat_out:
    :param targets:
    :param track_ids:
    :param model:
    :return:
    """
    ft = torch.cuda.FloatTensor if preds[0].is_cuda else torch.Tensor
    l_cls, l_box, l_obj, l_reid = ft([0]), ft([0]), ft([0]), ft([0])

    # build targets for loss_funcs computation
    t_cls, t_box, indices, anchor_vec, t_track_ids = build_targets_with_ids(preds, targets, track_ids, model)

    h = model.hyp  # hyper parameters
    red = 'mean'  # Loss reduction (sum or mean)

    # Define criteria
    BCE_cls = nn.BCEWithLogitsLoss(pos_weight=ft([h['cls_pw']]), reduction=red)
    BCE_obj = nn.BCEWithLogitsLoss(pos_weight=ft([h['obj_pw']]), reduction=red)
    CE_reid = nn.CrossEntropyLoss()

    # class label smoothing https://arxiv.org/pdf/1902.04103.pdf eqn 3
    cp, cn = smooth_BCE(eps=0.0)

    # focal loss_funcs
    g = h['fl_gamma']  # focal loss_funcs gamma
    if g > 0:
        BCE_cls, BCE_obj = FocalLoss(BCE_cls, g), FocalLoss(BCE_obj, g)

    np, ng = 0, 0  # number grid points, targets(GT)

    # Compute losses for each YOLO layer
    for i, pred_i in enumerate(preds):  # layer index, layer predictions
        id_map_w, id_map_h = reid_feat_out[i].shape[3], reid_feat_out[i].shape[2]

        ny, nx = pred_i.shape[2], pred_i.shape[3]
        b, a, gy, gx = indices[i]  # image, anchor, grid_y, grid_x
        tr_ids = t_track_ids[i]  # track ids
        cls_ids = t_cls[i]

        t_obj = torch.zeros_like(pred_i[..., 0])  # target obj(confidence score), e.g. 5×3×96×96
        np += t_obj.numel()  # total number of elements

        # Compute losses
        nb = len(b)  # number of targets(GT boxes)
        if nb:  # if exist GT box
            ng += nb

            # prediction subset corresponding to targets
            # specified item_i_in_batch, anchor_i, grid_y, grid_x
            pred_s = pred_i[b, a, gy, gx]  # nb × 10
            # pred_s[:, 2:4] = torch.sigmoid(pred_s[:, 2:4])  # wh power loss_funcs (uncomment)

            # GIoU
            pxy = torch.sigmoid(pred_s[:, 0:2])  # pxy = pxy * s - (s - 1) / 2,  s = 1.5  (scale_xy)
            pwh = torch.exp(pred_s[:, 2:4]).clamp(max=1E3) * anchor_vec[i]
            p_box = torch.cat((pxy, pwh), 1)  # predicted bounding box
            g_iou = bbox_iou(p_box.t(), t_box[i], x1y1x2y2=False, GIoU=True)  # g_iou computation: in YOLO layer's scale
            l_box += (1.0 - g_iou).sum() if red == 'sum' else (1.0 - g_iou).mean()  # g_iou loss_funcs
            t_obj[b, a, gy, gx] = (1.0 - model.gr) + model.gr * g_iou.detach().clamp(0).type(
                t_obj.dtype)  # g_iou ratio taken into account

            if model.nc > 1:  # cls loss_funcs (only if multiple classes)
                t = torch.full_like(pred_s[:, 5:], cn)  # targets: nb × num_classes
                t[range(nb), cls_ids] = cp
                l_cls += BCE_cls(pred_s[:, 5:], t)  # BCE loss for each object class
                # l_cls += CE(pred_s[:, 5:], cls_ids)  # CE

            # ----- compute reid loss_funcs for each GT box
            # get center point coordinates for all GT
            center_x = gx + pred_s[:, 0]
            center_y = gy + pred_s[:, 1]

            # convert to reid_feature map's scale
            center_x *= float(id_map_w) / float(nx)
            center_y *= float(id_map_h) / float(ny)

            # convert to int64 for indexing
            center_x += 0.5
            center_y += 0.5
            center_x = center_x.long()
            center_y = center_y.long()

            # avoid exceed reid feature map's range
            center_x.clamp_(0, id_map_w - 1)
            center_y.clamp_(0, id_map_h - 1)

            # get reid feature vector for GT boxes
            t_reid_feat_vects = reid_feat_out[i][b, :, center_y, center_x]  # nb × 128

            # ----- compute each object class's reid loss_funcs
            multi_gpu = type(model) in (nn.parallel.DataParallel, nn.parallel.DistributedDataParallel)
            if multi_gpu:
                for cls_id, id_num in model.module.max_id_dict.items():
                    inds = torch.where(cls_ids == cls_id)
                    if inds[0].shape[0] == 0:
                        # print('skip class id', cls_id)
                        continue

                    id_vects = t_reid_feat_vects[inds]
                    id_vects = F.normalize(id_vects, dim=1)  # L2 normalize the feature vector

                    fc_preds = model.module.id_classifiers[cls_id].forward(id_vects).contiguous()
                    l_reid += CE_reid(fc_preds, tr_ids[inds])
            else:
                for cls_id, id_num in model.max_id_dict.items():
                    inds = torch.where(cls_ids == cls_id)
                    if inds[0].shape[0] == 0:
                        # print('skip class id', cls_id)
                        continue

                    id_vects = t_reid_feat_vects[inds]
                    id_vects = F.normalize(id_vects, dim=1)  # L2 normalize the feature vector

                    if model.fc_type == 'FC':
                        ## normal FC layer as classifier
                        fc_preds = model.id_classifiers[cls_id].forward(id_vects).contiguous()
                        l_reid += CE_reid(fc_preds, tr_ids[inds])

                    elif model.fc_type == 'Arc':
                        ## arc margin FC layer as classifier
                        fc_preds = model.id_classifiers[cls_id].forward(id_vects, tr_ids[inds]).contiguous()
                        l_reid += CE_reid(fc_preds, tr_ids[inds])

            # Append targets to text file
            # with open('targets.txt', 'a') as file:
            #     [file.write('%11.5g ' * 4 % tuple(x) + '\n') for x in torch.cat((txy[i], twh[i]), 1)]

        l_obj += BCE_obj(pred_i[..., 4], t_obj)  # obj loss_funcs(confidence score loss_funcs)

    l_box *= h['giou']
    l_obj *= h['obj']
    l_cls *= h['cls']
    # l_reid *= h['reid']
    l_reid /= float(nb)  # reid loss_funcs normalize by number of GT objects

    if red == 'sum':
        bs = t_obj.shape[0]  # batch size
        l_obj *= 3 / (6300 * bs) * 2  # 3 / np * 2
        if ng:
            l_cls *= 3 / ng / model.nc
            l_box *= 3 / ng

    loss = l_box + l_obj + l_cls + l_reid
    return loss, torch.cat((l_box, l_obj, l_cls, l_reid, loss)).detach()


def compute_loss(preds, targets, model):  # predictions, targets, model
    """
    :param preds:
    :param targets:
    :param model:
    :return:
    """
    ft = torch.cuda.FloatTensor if preds[0].is_cuda else torch.Tensor

    l_cls, l_box, l_obj = ft([0]), ft([0]), ft([0])
    t_cls, t_box, indices, anchor_vec = build_targets(preds, targets, model)

    h = model.hyp  # hyper parameters
    red = 'mean'  # Loss reduction (sum or mean)

    # Define criteria
    BCE_cls = nn.BCEWithLogitsLoss(pos_weight=ft([h['cls_pw']]), reduction=red)
    BCE_obj = nn.BCEWithLogitsLoss(pos_weight=ft([h['obj_pw']]), reduction=red)

    # class label smoothing https://arxiv.org/pdf/1902.04103.pdf eqn 3
    cp, cn = smooth_BCE(eps=0.0)

    # focal loss_funcs
    g = h['fl_gamma']  # focal loss_funcs gamma
    if g > 0:
        BCE_cls, BCE_obj = FocalLoss(BCE_cls, g), FocalLoss(BCE_obj, g)

    # Compute losses
    np, ng = 0, 0  # number grid points, targets(GT)
    for i, pred_i in enumerate(preds):  # layer index, layer predictions
        b, a, gj, gi = indices[i]  # image, anchor, grid_y, grid_x
        t_obj = torch.zeros_like(pred_i[..., 0])  # target obj(confidence score), e.g. 5×3×96×96
        np += t_obj.numel()  # total number of elements

        # Compute losses
        nb = len(b)  # number of targets(GT boxes)
        if nb:  # if exist GT box
            ng += nb

            # prediction subset corresponding to targets
            # specified item_i_in_batch, anchor_i, grid_y, grid_x
            pred_s = pred_i[b, a, gj, gi]  # nb × 10
            # pred_s[:, 2:4] = torch.sigmoid(pred_s[:, 2:4])  # wh power loss_funcs (uncomment)

            # GIoU
            pxy = torch.sigmoid(pred_s[:, 0:2])  # pxy = pxy * s - (s - 1) / 2,  s = 1.5  (scale_xy)
            pwh = torch.exp(pred_s[:, 2:4]).clamp(max=1E3) * anchor_vec[i]
            p_box = torch.cat((pxy, pwh), 1)  # predicted bounding box
            g_iou = bbox_iou(p_box.t(), t_box[i], x1y1x2y2=False, GIoU=True)  # g_iou computation
            l_box += (1.0 - g_iou).sum() if red == 'sum' else (1.0 - g_iou).mean()  # g_iou loss_funcs
            t_obj[b, a, gj, gi] = (1.0 - model.gr) \
                                  + model.gr * g_iou.detach().clamp(0).type(
                t_obj.dtype)  # g_iou ratio taken into account

            if model.nc > 1:  # cls loss_funcs (only if multiple classes)
                t = torch.full_like(pred_s[:, 5:], cn)  # targets: nb × num_classes
                t[range(nb), t_cls[i]] = cp
                l_cls += BCE_cls(pred_s[:, 5:], t)  # BCE
                # l_cls += CE(pred_s[:, 5:], t_cls[i])  # CE

            # Append targets to text file
            # with open('targets.txt', 'a') as file:
            #     [file.write('%11.5g ' * 4 % tuple(x) + '\n') for x in torch.cat((txy[i], twh[i]), 1)]

        l_obj += BCE_obj(pred_i[..., 4], t_obj)  # obj loss_funcs(confidence score loss_funcs)

    l_box *= h['giou']
    l_obj *= h['obj']
    l_cls *= h['cls']
    if red == 'sum':
        bs = t_obj.shape[0]  # batch size
        l_obj *= 3 / (6300 * bs) * 2  # 3 / np * 2
        if ng:
            l_cls *= 3 / ng / model.nc
            l_box *= 3 / ng

    loss = l_box + l_obj + l_cls
    return loss, torch.cat((l_box, l_obj, l_cls, loss)).detach()


def build_targets_with_ids(preds, targets, track_ids, model):
    """
    :param preds:
    :param targets:
    :param track_ids:
    :param model:
    :return:
    """
    # targets = [image, class, x, y, w, h]

    nt = targets.shape[0]
    t_cls, t_box, indices, av, t_track_ids = [], [], [], [], []
    reject, use_all_anchors = True, True
    gain = torch.ones(6, device=targets.device)  # normalized to grid space gain

    # m = list(model.modules())[-1]
    # for i in range(m.nl):
    #    anchors = m.anchors[i]
    multi_gpu = type(model) in (nn.parallel.DataParallel, nn.parallel.DistributedDataParallel)

    # build each YOLO layer of corresponding scale
    for i, idx in enumerate(model.yolo_layer_inds):
        # get number of grid points and anchor vec for this YOLO layer:
        # anchors in YOLO layer(feature map)'s scale
        anchors = model.module.module_list[idx].anchor_vec if multi_gpu else model.module_list[idx].anchor_vec

        # iou of targets-anchors
        gain[2:] = torch.tensor(preds[i].shape)[[3, 2, 3, 2]]  # xyxy gain
        t, a = targets * gain, []
        gwh = t[:, 4:6]  # targets(GT): bbox_w, bbox_h in yolo layer(feature map)'s scale
        if nt:
            iou = wh_iou(anchors, gwh)  # iou(3,n) = wh_iou(anchors(3,2), gwh(n,2))

            if use_all_anchors:
                na = anchors.shape[0]  # number of anchors
                a = torch.arange(na).view(-1, 1).repeat(1, nt).view(
                    -1)  # anchor index, N_a × N_gt_box:e.g. 56个0, 56个1, 56个2
                t = t.repeat(na, 1)  # 56 × 6 -> (56×3) × 6
                tr_ids = track_ids.repeat(na)  # 56 -> 56×3
            else:  # use best anchor only
                iou, a = iou.max(0)  # best iou and anchor

            # reject anchors below iou_thres (OPTIONAL, increases P, lowers R)
            if reject:
                # get index whose anchor and gt box's iou exceeds the iou threshold,
                # defined as positive sample
                idx = iou.view(-1) > model.hyp['iou_t']  # iou threshold hyper parameter
                t, a = t[idx], a[idx]

                # GT track ids: for reid classification training
                tr_ids = tr_ids[idx]

        # Indices
        b, c = t[:, :2].long().t()  # target image index in the batch, class id
        gxy = t[:, 2:4]  # grid x, y (GT center)
        gwh = t[:, 4:6]  # grid w, h
        gi, gj = gxy.long().t()  # grid x, y indices(int64), .t(): transpose a matrix
        indices.append((b, a, gj, gi))

        # Box
        gxy -= gxy.floor()  # GT box center xy 's fractional part
        t_box.append(torch.cat((gxy, gwh), 1))  # xywh (grids)
        av.append(anchors[a])  # anchor vectors of corresponding GT boxes

        # GT track ids
        t_track_ids.append(tr_ids)

        # GT Class ids
        t_cls.append(c)
        if c.shape[0]:  # if any targets
            assert c.max() < model.nc, \
                'Model accepts %g classes labeled from 0-%g, however you labelled a class %g. ' \
                'See https://github.com/ultralytics/yolov3/wiki/Train-Custom-Data' % (
                    model.nc, model.nc - 1, c.max())

    return t_cls, t_box, indices, av, t_track_ids


def build_targets(preds, targets, model):
    # targets = [image, class, x, y, w, h]

    nt = targets.shape[0]
    t_cls, t_box, indices, av = [], [], [], []
    reject, use_all_anchors = True, True
    gain = torch.ones(6, device=targets.device)  # normalized to grid space gain

    # m = list(model.modules())[-1]
    # for i in range(m.nl):
    #    anchors = m.anchors[i]
    multi_gpu = type(model) in (nn.parallel.DataParallel, nn.parallel.DistributedDataParallel)
    for i, idx in enumerate(model.yolo_layer_inds):  # each YOLO layer of corresponding scale
        # get number of grid points and anchor vec for this YOLO layer:
        # anchors in YOLO layer(feature map)'s scale
        anchors = model.module.module_list[idx].anchor_vec if multi_gpu else model.module_list[idx].anchor_vec

        # iou of targets-anchors
        gain[2:] = torch.tensor(preds[i].shape)[[3, 2, 3, 2]]  # xyxy gain
        t, a = targets * gain, []
        gwh = t[:, 4:6]  # targets(GT): bbox_w, bbox_h in yolo layer(feature map)'s scale
        if nt:
            iou = wh_iou(anchors, gwh)  # iou(3,n) = wh_iou(anchors(3,2), gwh(n,2))

            if use_all_anchors:
                na = anchors.shape[0]  # number of anchors
                a = torch.arange(na).view(-1, 1).repeat(1, nt).view(
                    -1)  # anchor index, N_a × N_gt_box:e.g. 56个0, 56个1, 56个2
                t = t.repeat(na, 1)  # 56 × 6 -> (56×3) × 6
            else:  # use best anchor only
                iou, a = iou.max(0)  # best iou and anchor

            # reject anchors below iou_thres (OPTIONAL, increases P, lowers R)
            if reject:
                # get index whose anchor and gt box's iou exceeds the iou threshold,
                # defined as positive sample
                idx = iou.view(-1) > model.hyp['iou_t']  # iou threshold hyper parameter
                t, a = t[idx], a[idx]

        # Indices
        b, c = t[:, :2].long().t()  # target image index in the batch, class id
        gxy = t[:, 2:4]  # grid x, y (GT center)
        gwh = t[:, 4:6]  # grid w, h
        gi, gj = gxy.long().t()  # grid x, y indices(int64), .t(): transpose a matrix
        indices.append((b, a, gj, gi))

        # Box
        gxy -= gxy.floor()  # GT box center xy 's fractional part
        t_box.append(torch.cat((gxy, gwh), 1))  # xywh (grids)
        av.append(anchors[a])  # anchor vectors of corresponding GT boxes

        # GT Class ids
        t_cls.append(c)
        if c.shape[0]:  # if any targets
            assert c.max() < model.nc, \
                'Model accepts %g classes labeled from 0-%g, however you labelled a class %g. ' \
                'See https://github.com/ultralytics/yolov3/wiki/Train-Custom-Data' % (
                    model.nc, model.nc - 1, c.max())

    return t_cls, t_box, indices, av


def non_max_suppression_debug(predictions,
                              yolo_inds,
                              grids,
                              conf_thres=0.1,
                              iou_thres=0.6,
                              merge=False,
                              classes=None,
                              agnostic=False):
    """Performs Non-Maximum Suppression (NMS) on inference results

    Returns:
         detections with shape: nx6 (x1, y1, x2, y2, conf, cls)
    """
    if predictions.dtype is torch.float16:
        predictions = predictions.float()  # to FP32

    nc = predictions[0].shape[1] - 5  # number of classes
    xc = predictions[..., 4] > conf_thres  # candidates

    # Settings
    min_wh, max_wh = 2, 4096  # (pixels) minimum and maximum box width and height
    max_det = 300  # maximum number of detections per image
    time_limit = 10.0  # seconds to quit after
    redundant = True  # require redundant detections
    multi_label = nc > 1  # multiple labels per box (adds 0.5ms/img)

    # t = time.time()
    output = [None] * predictions.shape[0]
    output_yolo_inds = [None] * predictions.shape[0]
    out_girds = [None] * predictions.shape[0]

    for xi, x in enumerate(predictions):  # xi: image index in the batch, image inference
        # Apply constraints
        # x[((x[..., 2:4] < min_wh) | (x[..., 2:4] > max_wh)).any(1), 4] = 0  # width-height
        x = x[xc[xi]]  # confidence
        yolo_inds = yolo_inds[xi][xc[xi]]
        grids = grids[xi][xc[xi]]

        # If none remain process next image
        if not x.shape[0]:
            continue

        # Compute conf
        x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf(目标概率*前景概率)

        # Box (center x, center y, width, height) to (x1, y1, x2, y2)
        box = xywh2xyxy(x[:, :4])

        # Detections matrix nx6 (xyxy, conf, cls)
        if multi_label:
            i, j = (x[:, 5:] > conf_thres).nonzero(as_tupe=False).t()

            boxes = box[i]
            cls_scores = x[i, j + 5, None]
            cls_inds = j[:, None].float()

            yolo_inds = yolo_inds[i]
            grids = grids[i]

            # x = torch.cat((box[i], x[i, j + 5, None], j[:, None].float()), 1)
            x = torch.cat((boxes, cls_scores, cls_inds), 1)  # box(4), cls_score(1), cls_id(1): n×6

        else:  # best class only
            conf, j = x[:, 5:].max(1, keepdim=True)
            x = torch.cat((box, conf, j.float()), 1)[conf.view(-1) > conf_thres]

        # Filter by class
        if classes:
            x = x[(x[:, 5:6] == torch.tensor(classes, device=x.device)).any(1)]

        # Apply finite constraint
        # if not torch.isfinite(x).all():
        #     x = x[torch.isfinite(x).all(1)]

        # If none remain process next image
        n = x.shape[0]  # number of boxes
        if not n:
            continue

        # Sort by confidence
        # x = x[x[:, 4].argsort(descending=True)]

        # Batched NMS
        c = x[:, 5:6] * (0 if agnostic else max_wh)  # classes
        boxes, scores = x[:, :4] + c, x[:, 4]  # boxes (offset by class), scores
        i = torchvision.ops.boxes.nms(boxes, scores, iou_thres)
        if i.shape[0] > max_det:  # limit detections
            i = i[:max_det]
        if merge and (1 < n < 3E3):  # Merge NMS (boxes merged using weighted mean)
            try:  # update boxes as boxes(i,4) = weights(i,n) * boxes(n,4)
                iou = box_iou(boxes[i], boxes) > iou_thres  # iou matrix
                weights = iou * scores[None]  # box weights
                x[i, :4] = torch.mm(weights, x[:, :4]).float() / weights.sum(1, keepdim=True)  # merged boxes
                if redundant:
                    i = i[iou.sum(1) > 1]  # require redundancy
            except:  # possible CUDA error https://github.com/ultralytics/yolov3/issues/1139
                print(x, i, x.shape, i.shape)
                pass

        output[xi] = x[i]
        # if (time.time() - t) > time_limit:
        #    break  # time limit exceeded

        output_yolo_inds[xi] = yolo_inds[i]
        out_girds[xi] = grids[i]

    return output, output_yolo_inds, out_girds


def non_max_suppression_with_yolo_inds(predictions,
                                       yolo_inds,
                                       conf_thres=0.1,
                                       iou_thres=0.6,
                                       merge=False,
                                       classes=None,
                                       agnostic=False):
    """Performs Non-Maximum Suppression (NMS) on inference results

    Returns:
         detections with shape: nx6 (x1, y1, x2, y2, conf, cls)
    """
    if predictions.dtype is torch.float16:
        predictions = predictions.float()  # to FP32

    nc = predictions[0].shape[1] - 5  # number of classes
    xc = predictions[..., 4] > conf_thres  # candidates

    # Settings
    min_wh, max_wh = 2, 4096  # (pixels) minimum and maximum box width and height
    max_det = 300  # maximum number of detections per image
    time_limit = 10.0  # seconds to quit after
    redundant = True  # require redundant detections
    multi_label = nc > 1  # multiple labels per box (adds 0.5ms/img)

    # t = time.time()
    output = [None] * predictions.shape[0]
    output_yolo_inds = [None] * predictions.shape[0]
    for xi, x in enumerate(predictions):  # xi: image index in the batch, image inference
        # Apply constraints
        # x[((x[..., 2:4] < min_wh) | (x[..., 2:4] > max_wh)).any(1), 4] = 0  # width-height
        x = x[xc[xi]]  # confidence
        yolo_inds = yolo_inds[xi][xc[xi]]

        # If none remain process next image
        if not x.shape[0]:
            continue

        # Compute conf
        x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf(目标概率*前景概率)

        # Box (center x, center y, width, height) to (x1, y1, x2, y2)
        box = xywh2xyxy(x[:, :4])

        # Detections matrix nx6 (xyxy, conf, cls)
        if multi_label:
            i, j = (x[:, 5:] > conf_thres).nonzero(as_tuple=False).t()

            boxes = box[i]
            cls_scores = x[i, j + 5, None]
            cls_inds = j[:, None].float()

            yolo_inds = yolo_inds[i]

            # x = torch.cat((box[i], x[i, j + 5, None], j[:, None].float()), 1)
            x = torch.cat((boxes, cls_scores, cls_inds), 1)  # box(4), cls_score(1), cls_id(1): n×6

        else:  # best class only
            conf, j = x[:, 5:].max(1, keepdim=True)
            x = torch.cat((box, conf, j.float()), 1)[conf.view(-1) > conf_thres]

        # Filter by class
        if classes:
            x = x[(x[:, 5:6] == torch.tensor(classes, device=x.device)).any(1)]

        # Apply finite constraint
        # if not torch.isfinite(x).all():
        #     x = x[torch.isfinite(x).all(1)]

        # If none remain process next image
        n = x.shape[0]  # number of boxes
        if not n:
            continue

        # Sort by confidence
        # x = x[x[:, 4].argsort(descending=True)]

        # Batched NMS
        c = x[:, 5:6] * (0 if agnostic else max_wh)  # classes
        boxes, scores = x[:, :4] + c, x[:, 4]  # boxes (offset by class), scores
        i = torchvision.ops.boxes.nms(boxes, scores, iou_thres)
        if i.shape[0] > max_det:  # limit detections
            i = i[:max_det]
        if merge and (1 < n < 3E3):  # Merge NMS (boxes merged using weighted mean)
            try:  # update boxes as boxes(i,4) = weights(i,n) * boxes(n,4)
                iou = box_iou(boxes[i], boxes) > iou_thres  # iou matrix
                weights = iou * scores[None]  # box weights
                x[i, :4] = torch.mm(weights, x[:, :4]).float() / weights.sum(1, keepdim=True)  # merged boxes
                if redundant:
                    i = i[iou.sum(1) > 1]  # require redundancy
            except:  # possible CUDA error https://github.com/ultralytics/yolov3/issues/1139
                print(x, i, x.shape, i.shape)
                pass

        output[xi] = x[i]
        # if (time.time() - t) > time_limit:
        #    break  # time limit exceeded

        output_yolo_inds[xi] = yolo_inds[i]

    return output, output_yolo_inds


def non_max_suppression(predictions,
                        conf_thres=0.1,
                        iou_thres=0.6,
                        merge=False,
                        classes=None,
                        agnostic=False):
    """
    Performs Non-Maximum Suppression (NMS) on inference results
    Returns:
         detections with shape: nx6 (x1, y1, x2, y2, conf, cls)
    :param predictions:
    :param conf_thres:
    :param iou_thres:
    :param merge:
    :param classes:
    :param agnostic:
    :return:
    """
    if predictions.dtype is torch.float16:
        predictions = predictions.float()  # to FP32

    nc = predictions[0].shape[1] - 5  # number of classes
    xc = predictions[..., 4] > conf_thres  # candidates

    # Settings
    min_wh, max_wh = 2, 4096  # (pixels) minimum and maximum box width and height
    max_det = 300  # maximum number of detections per image
    time_limit = 10.0  # seconds to quit after
    redundant = True  # require redundant detections
    multi_label = nc > 1  # multiple labels per box (adds 0.5ms/img)

    # t = time.time()
    output = [None] * predictions.shape[0]
    for xi, x in enumerate(predictions):  # image index, image inference
        # Apply constraints
        # x[((x[..., 2:4] < min_wh) | (x[..., 2:4] > max_wh)).any(1), 4] = 0  # width-height
        x = x[xc[xi]]  # confidence

        # If none remain process next image
        if not x.shape[0]:
            continue

        # Compute conf
        x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf

        # Box (center x, center y, width, height) to (x1, y1, x2, y2)
        box = xywh2xyxy(x[:, :4])

        # Detections matrix nx6 (xyxy, conf, cls)
        if multi_label:
            i, j = (x[:, 5:] > conf_thres).nonzero(as_tuple=False).t()
            x = torch.cat((box[i], x[i, j + 5, None], j[:, None].float()), 1)
        else:  # best class only
            conf, j = x[:, 5:].max(1, keepdim=True)  # 只取概率最大的类别作为最终的类别
            x = torch.cat((box, conf, j.float()), 1)[conf.view(-1) > conf_thres]

        # Filter by class
        if classes:
            x = x[(x[:, 5:6] == torch.tensor(classes, device=x.device)).any(1)]

        # Apply finite constraint
        # if not torch.isfinite(x).all():
        #     x = x[torch.isfinite(x).all(1)]

        # If none remain process next image
        n = x.shape[0]  # number of boxes
        if not n:
            continue

        # Sort by confidence
        # x = x[x[:, 4].argsort(descending=True)]

        # Batched NMS
        c = x[:, 5:6] * (0 if agnostic else max_wh)  # classes
        boxes, scores = x[:, :4] + c, x[:, 4]  # boxes (offset by class), scores
        i = torchvision.ops.boxes.nms(boxes, scores, iou_thres)
        if i.shape[0] > max_det:  # limit detections
            i = i[:max_det]
        if merge and (1 < n < 3E3):  # Merge NMS (boxes merged using weighted mean)
            try:  # update boxes as boxes(i,4) = weights(i,n) * boxes(n,4)
                iou = box_iou(boxes[i], boxes) > iou_thres  # iou matrix
                weights = iou * scores[None]  # box weights
                x[i, :4] = torch.mm(weights, x[:, :4]).float() / weights.sum(1, keepdim=True)  # merged boxes
                if redundant:
                    i = i[iou.sum(1) > 1]  # require redundancy
            except:  # possible CUDA error https://github.com/ultralytics/yolov3/issues/1139
                print(x, i, x.shape, i.shape)
                pass

        output[xi] = x[i]
        # if (time.time() - t) > time_limit:
        #    break  # time limit exceeded

    return output


def get_yolo_layers(model):
    """
    :param model:
    :return:
    """
    bool_vec = [x['type'] == 'yolo' for x in model.module_defs]
    return [i for i, x in enumerate(bool_vec) if x]  # [82, 94, 106] for yolov3


def print_model_biases(model):
    # prints the bias neurons preceding each yolo layer
    print('\nModel Bias Summary: %8s%18s%18s%18s' % ('layer', 'regression', 'objectness', 'classification'))
    try:
        multi_gpu = type(model) in (nn.parallel.DataParallel, nn.parallel.DistributedDataParallel)
        for l in model.yolo_layer_inds:  # print pretrained biases
            if multi_gpu:
                na = model.module.module_list[l].na  # number of anchors
                b = model.module.module_list[l - 1][0].bias.view(na, -1)  # bias 3x85
            else:
                na = model.module_list[l].na
                b = model.module_list[l - 1][0].bias.view(na, -1)  # bias 3x85
            print(' ' * 20 + '%8g %18s%18s%18s' % (l, '%5.2f+/-%-5.2f' % (b[:, :4].mean(), b[:, :4].std()),
                                                   '%5.2f+/-%-5.2f' % (b[:, 4].mean(), b[:, 4].std()),
                                                   '%5.2f+/-%-5.2f' % (b[:, 5:].mean(), b[:, 5:].std())))
    except:
        pass


def strip_optimizer(f='weights/last.pt'):  # from evaluate_utils.evaluate_utils import *; strip_optimizer()
    # Strip optimizer from *.pt files for lighter files (reduced by 2/3 size)
    x = torch.load(f, map_location=torch.device('cpu'))
    x['optimizer'] = None
    torch.save(x, f)


def create_backbone(f='weights/last.pt'):  # from evaluate_utils.evaluate_utils import *; create_backbone()
    # create a backbone from a *.pt file
    x = torch.load(f, map_location=torch.device('cpu'))
    x['optimizer'] = None
    x['training_results'] = None
    x['epoch'] = -1
    for p in x['model'].values():
        try:
            p.requires_grad = True
        except:
            pass
    torch.save(x, 'weights/backbone.pt')


def coco_class_count(path='../coco/labels/train2014/'):
    # Histogram of occurrences per class
    nc = 80  # number classes
    x = np.zeros(nc, dtype='int32')
    files = sorted(glob.glob('%s/*.*' % path))
    for i, file in enumerate(files):
        labels = np.loadtxt(file, dtype=np.float32).reshape(-1, 5)
        x += np.bincount(labels[:, 0].astype('int32'), minlength=nc)
        print(i, len(files))


def coco_only_people(
        path='../coco/labels/train2017/'):  # from evaluate_utils.evaluate_utils import *; coco_only_people()
    # Find images with only people
    files = sorted(glob.glob('%s/*.*' % path))
    for i, file in enumerate(files):
        labels = np.loadtxt(file, dtype=np.float32).reshape(-1, 5)
        if all(labels[:, 0] == 0):
            print(labels.shape[0], file)


def select_best_evolve(path='evolve*.txt'):  # from evaluate_utils.evaluate_utils import *; select_best_evolve()
    # Find best evolved mutation
    for file in sorted(glob.glob(path)):
        x = np.loadtxt(file, dtype=np.float32, ndmin=2)
        print(file, x[fitness(x).argmax()])


def crop_images_random(path='../images/',
                       scale=0.50):  # from evaluate_utils.evaluate_utils import *; crop_images_random()
    # crops images into random squares up to scale fraction
    # WARNING: overwrites images!
    for file in tqdm(sorted(glob.glob('%s/*.*' % path))):
        img = cv2.imread(file)  # BGR
        if img is not None:
            h, w = img.shape[:2]

            # create random mask
            a = 30  # minimum size (pixels)
            mask_h = random.randint(a, int(max(a, h * scale)))  # mask height
            mask_w = mask_h  # mask width

            # box
            xmin = max(0, random.randint(0, w) - mask_w // 2)
            ymin = max(0, random.randint(0, h) - mask_h // 2)
            xmax = min(w, xmin + mask_w)
            ymax = min(h, ymin + mask_h)

            # apply random color mask
            cv2.imwrite(file, img[ymin:ymax, xmin:xmax])


def coco_single_class_labels(path='../coco/labels/train2014/', label_class=43):
    # Makes single-class coco datasets. from evaluate_utils.evaluate_utils import *; coco_single_class_labels()
    if os.path.exists('new/'):
        shutil.rmtree('new/')  # delete output folder
    os.makedirs('new/')  # make new output folder
    os.makedirs('new/labels/')
    os.makedirs('new/images/')
    for file in tqdm(sorted(glob.glob('%s/*.*' % path))):
        with open(file, 'r') as f:
            labels = np.array([x.split() for x in f.read().splitlines()], dtype=np.float32)
        i = labels[:, 0] == label_class
        if any(i):
            img_file = file.replace('labels', 'images').replace('txt', 'jpg')
            labels[:, 0] = 0  # reset class to 0
            with open('new/images.txt', 'a') as f:  # add image to dataset list
                f.write(img_file + '\n')
            with open('new/labels/' + Path(file).name, 'a') as f:  # write label
                for l in labels[i]:
                    f.write('%g %.6f %.6f %.6f %.6f\n' % tuple(l))
            shutil.copyfile(src=img_file, dst='new/images/' + Path(file).name.replace('txt', 'jpg'))  # copy images


def kmean_anchors(path='../coco/train2017.txt', n=12, img_size=(320, 1024), thr=0.10, gen=1000):
    # Creates kmeans anchors for use in *.cfg files: from evaluate_utils.evaluate_utils import *; _ = kmean_anchors()
    # n: number of anchors
    # img_size: (min, max) image size used for multi-scale training (can be same values)
    # thr: IoU threshold hyperparameter used for training (0.0 - 1.0)
    # gen: generations to evolve anchors using genetic algorithm
    from utils.datasets import LoadImagesAndLabels

    def print_results(k):
        k = k[np.argsort(k.prod(1))]  # sort small to large
        iou = wh_iou(wh, torch.Tensor(k))
        max_iou = iou.max(1)[0]
        bpr, aat = (max_iou > thr).float().mean(), (iou > thr).float().mean() * n  # best possible recall, anch > thr
        print('%.2f iou_thr: %.3f best possible recall, %.2f anchors > thr' % (thr, bpr, aat))
        print('n=%g, img_size=%s, IoU_all=%.3f/%.3f-mean/best, IoU>thr=%.3f-mean: ' %
              (n, img_size, iou.mean(), max_iou.mean(), iou[iou > thr].mean()), end='')
        for i, x in enumerate(k):
            print('%i,%i' % (round(x[0]), round(x[1])), end=',  ' if i < len(k) - 1 else '\n')  # use in *.cfg
        return k

    def fitness(k):  # mutation fitness
        iou = wh_iou(wh, torch.Tensor(k))  # iou
        max_iou = iou.max(1)[0]
        return (max_iou * (max_iou > thr).float()).mean()  # product

    # Get label wh
    wh = []
    dataset = LoadImagesAndLabels(path, augment=True, rect=True, cache_labels=True)
    nr = 1 if img_size[0] == img_size[1] else 10  # number augmentation repetitions
    for s, l in zip(dataset.shapes, dataset.labels):
        wh.append(l[:, 3:5] * (s / s.max()))  # image normalized to letterbox normalized wh
    wh = np.concatenate(wh, 0).repeat(nr, axis=0)  # augment 10x
    wh *= np.random.uniform(img_size[0], img_size[1], size=(wh.shape[0], 1))  # normalized to pixels (multi-scale)
    wh = wh[(wh > 2.0).all(1)]  # remove below threshold boxes (< 2 pixels wh)

    # Darknet yolov3.cfg anchors
    use_darknet = False
    if use_darknet and n == 9:
        k = np.array([[10, 13], [16, 30], [33, 23], [30, 61], [62, 45], [59, 119], [116, 90], [156, 198], [373, 326]])
    else:
        # Kmeans calculation
        from scipy.cluster.vq import kmeans
        print('Running kmeans for %g anchors on %g points...' % (n, len(wh)))
        s = wh.std(0)  # sigmas for whitening
        k, dist = kmeans(wh / s, n, iter=30)  # points, mean distance
        k *= s
    wh = torch.Tensor(wh)
    k = print_results(k)

    # # Plot
    # k, d = [None] * 20, [None] * 20
    # for i in tqdm(range(1, 21)):
    #     k[i-1], d[i-1] = kmeans(wh / s, i)  # points, mean distance
    # fig, ax = plt.subplots(1, 2, figsize=(14, 7))
    # ax = ax.ravel()
    # ax[0].plot(np.arange(1, 21), np.array(d) ** 2, marker='.')
    # fig, ax = plt.subplots(1, 2, figsize=(14, 7))  # plot wh
    # ax[0].hist(wh[wh[:, 0]<100, 0],400)
    # ax[1].hist(wh[wh[:, 1]<100, 1],400)
    # fig.tight_layout()
    # fig.savefig('wh.png', dpi=200)

    # Evolve
    npr = np.random
    f, sh, mp, s = fitness(k), k.shape, 0.9, 0.1  # fitness, generations, mutation prob, sigma
    for _ in tqdm(range(gen), desc='Evolving anchors'):
        v = np.ones(sh)
        while (v == 1).all():  # mutate until a change occurs (prevent duplicates)
            v = ((npr.random(sh) < mp) * npr.random() * npr.randn(*sh) * s + 1).clip(0.3, 3.0)  # 98.6, 61.6
        kg = (k.copy() * v).clip(min=2.0)
        fg = fitness(kg)
        if fg > f:
            f, k = fg, kg.copy()
            print_results(k)
    k = print_results(k)

    return k


def print_mutation(hyp, results, bucket=''):
    # Print mutation results to evolve.txt (for use with train.py --evolve)
    a = '%10s' * len(hyp) % tuple(hyp.keys())  # hyperparam keys
    b = '%10.3g' * len(hyp) % tuple(hyp.values())  # hyperparam values
    c = '%10.4g' * len(results) % results  # results (P, R, mAP, F1, test_loss)
    print('\n%s\n%s\nEvolved fitness: %s\n' % (a, b, c))

    if bucket:
        os.system('gsutil cp gs://%s/evolve.txt .' % bucket)  # download evolve.txt

    with open('evolve.txt', 'a') as f:  # append result
        f.write(c + b + '\n')
    x = np.unique(np.loadtxt('evolve.txt', ndmin=2), axis=0)  # load unique rows
    np.savetxt('evolve.txt', x[np.argsort(-fitness(x))], '%10.3g')  # save sort by fitness

    if bucket:
        os.system('gsutil cp evolve.txt gs://%s' % bucket)  # upload evolve.txt


def apply_classifier(x, model, img, im0):
    """
    :param x:
    :param model:
    :param img:
    :param im0:
    :return:
    """
    # applies a second stage classifier to yolo outputs
    im0 = [im0] if isinstance(im0, np.ndarray) else im0
    for i, d in enumerate(x):  # per image
        if d is not None and len(d):
            d = d.clone()

            # Reshape and pad cutouts
            b = xyxy2xywh(d[:, :4])  # boxes
            b[:, 2:] = b[:, 2:].max(1)[0].unsqueeze(1)  # rectangle to square
            b[:, 2:] = b[:, 2:] * 1.3 + 30  # pad
            d[:, :4] = xywh2xyxy(b).long()

            # Rescale boxes from img_size to im0 size
            scale_coords(img.shape[2:], d[:, :4], im0[i].shape)

            # Classes
            pred_cls1 = d[:, 5].long()
            ims = []
            for j, a in enumerate(d):  # per item
                cutout = im0[i][int(a[1]):int(a[3]), int(a[0]):int(a[2])]
                im = cv2.resize(cutout, (224, 224))  # BGR
                # cv2.imwrite('test%i.jpg' % j, cutout)

                im = im[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, to 3x416x416
                im = np.ascontiguousarray(im, dtype=np.float32)  # uint8 to float32
                im /= 255.0  # 0 - 255 to 0.0 - 1.0
                ims.append(im)

            pred_cls2 = model(torch.Tensor(ims).to(d.device)).argmax(1)  # classifier prediction
            x[i] = x[i][pred_cls1 == pred_cls2]  # retain matching class detections

    return x


def fitness(x):
    """
    :param x:
    :return:
    """
    # Returns fitness (for use with results.txt or evolve.txt)
    w = [0.0, 0.01, 0.99, 0.00]  # weights for [P, R, mAP, F1]@0.5 or [P, R, mAP@0.5, mAP@0.5:0.95]
    return (x[:, :4] * w).sum(1)


# Plotting functions ---------------------------------------------------------------------------------------------------
def plot_one_box(x, img, color=None, label=None, line_thickness=None):
    """
    :param x:
    :param img:
    :param color:
    :param label:
    :param line_thickness:
    :return:
    """
    # Plots one bounding box on image img
    tl = line_thickness or round(0.002 * (img.shape[0] + img.shape[1]) / 2) + 1  # line/font thickness
    color = color or [random.randint(0, 255) for _ in range(3)]
    c1, c2 = (int(x[0]), int(x[1])), (int(x[2]), int(x[3]))
    cv2.rectangle(img, c1, c2, color, thickness=tl)
    if label:
        tf = max(tl - 1, 1)  # font thickness
        t_size = cv2.getTextSize(label, 0, fontScale=tl / 3, thickness=tf)[0]
        c2 = c1[0] + t_size[0], c1[1] - t_size[1] - 3
        cv2.rectangle(img, c1, c2, color, -1)  # filled
        cv2.putText(img, label, (c1[0], c1[1] - 2), 0, tl / 3, [225, 255, 255], thickness=tf, lineType=cv2.LINE_AA)


def plot_wh_methods():  # from evaluate_utils.evaluate_utils import *; plot_wh_methods()
    # Compares the two methods for width-height anchor multiplication
    # https://github.com/ultralytics/yolov3/issues/168
    x = np.arange(-4.0, 4.0, .1)
    ya = np.exp(x)
    yb = torch.sigmoid(torch.from_numpy(x)).numpy() * 2

    fig = plt.figure(figsize=(6, 3), dpi=150)
    plt.plot(x, ya, '.-', label='yolo method')
    plt.plot(x, yb ** 2, '.-', label='^2 power method')
    plt.plot(x, yb ** 2.5, '.-', label='^2.5 power method')
    plt.xlim(left=-4, right=4)
    plt.ylim(bottom=0, top=6)
    plt.xlabel('input')
    plt.ylabel('output')
    plt.legend()
    fig.tight_layout()
    fig.savefig('comparison.png', dpi=200)


def plot_images(imgs, targets, paths=None, fname='images.png'):
    # Plots training images overlaid with targets
    imgs = imgs.cpu().numpy()
    targets = targets.cpu().numpy()
    # targets = targets[targets[:, 1] == 21]  # plot only one class

    fig = plt.figure(figsize=(10, 10))
    bs, _, h, w = imgs.shape  # batch size, _, height, width
    bs = min(bs, 16)  # limit plot to 16 images
    ns = np.ceil(bs ** 0.5)  # number of subplots

    for i in range(bs):
        boxes = xywh2xyxy(targets[targets[:, 0] == i, 2:6]).T
        boxes[[0, 2]] *= w
        boxes[[1, 3]] *= h
        plt.subplot(int(ns), int(ns), int(i + 1)).imshow(imgs[i].transpose(1, 2, 0))
        plt.plot(boxes[[0, 2, 2, 0, 0]], boxes[[1, 1, 3, 3, 1]], '.-')
        plt.axis('off')
        if paths is not None:
            s = Path(paths[i]).name
            plt.title(s[:min(len(s), 40)], fontdict={'size': 8})  # limit to 40 characters
    fig.tight_layout()
    fig.savefig(fname, dpi=200)
    plt.close()


def plot_test_txt():  # from evaluate_utils.evaluate_utils import *; plot_test()
    # Plot test.txt histograms
    x = np.loadtxt('test.txt', dtype=np.float32)
    box = xyxy2xywh(x[:, :4])
    cx, cy = box[:, 0], box[:, 1]

    fig, ax = plt.subplots(1, 1, figsize=(6, 6))
    ax.hist2d(cx, cy, bins=600, cmax=10, cmin=0)
    ax.set_aspect('equal')
    fig.tight_layout()
    plt.savefig('hist2d.png', dpi=300)

    fig, ax = plt.subplots(1, 2, figsize=(12, 6))
    ax[0].hist(cx, bins=600)
    ax[1].hist(cy, bins=600)
    fig.tight_layout()
    plt.savefig('hist1d.png', dpi=200)


def plot_targets_txt():  # from evaluate_utils.evaluate_utils import *; plot_targets_txt()
    # Plot targets.txt histograms
    x = np.loadtxt('targets.txt', dtype=np.float32).T
    s = ['x targets', 'y targets', 'width targets', 'height targets']
    fig, ax = plt.subplots(2, 2, figsize=(8, 8))
    ax = ax.ravel()
    for i in range(4):
        ax[i].hist(x[i], bins=100, label='%.3g +/- %.3g' % (x[i].mean(), x[i].std()))
        ax[i].legend()
        ax[i].set_title(s[i])
    fig.tight_layout()
    plt.savefig('targets.jpg', dpi=200)


def plot_evolution_results(hyp):  # from evaluate_utils.evaluate_utils import *; plot_evolution_results(hyp)
    """
    :param hyp:
    :return:
    """
    # Plot hyperparameter evolution results in evolve.txt
    x = np.loadtxt('evolve.txt', ndmin=2)
    f = fitness(x)
    weights = (f - f.min()) ** 2  # for weighted results
    fig = plt.figure(figsize=(12, 10))
    matplotlib.rc('font', **{'size': 8})
    for i, (k, v) in enumerate(hyp.items()):
        y = x[:, i + 7]
        # mu = (y * weights).sum() / weights.sum()  # best weighted result
        mu = y[f.argmax()]  # best single result
        plt.subplot(4, 5, i + 1)
        plt.plot(mu, f.max(), 'o', markersize=10)
        plt.plot(y, f, '.')
        plt.title('%s = %.3g' % (k, mu), fontdict={'size': 9})  # limit to 40 characters
        print('%15s: %.3g' % (k, mu))
    fig.tight_layout()
    plt.savefig('evolve.png', dpi=200)


def plot_results_overlay(start=0, stop=0):  # from evaluate_utils.evaluate_utils import *; plot_results_overlay()
    """
    :param start:
    :param stop:
    :return:
    """
    # Plot training results files 'results*.txt', overlaying train and val losses
    s = ['train', 'train', 'train', 'Precision', 'mAP@0.5', 'val', 'val', 'val', 'Recall', 'F1']  # legends
    t = ['GIoU', 'Objectness', 'Classification', 'P-R', 'mAP-F1']  # titles
    for f in sorted(glob.glob('results*.txt') + glob.glob('../../Downloads/results*.txt')):
        results = np.loadtxt(f, usecols=[2, 3, 4, 8, 9, 12, 13, 14, 10, 11], ndmin=2).T
        n = results.shape[1]  # number of rows
        x = range(start, min(stop, n) if stop else n)
        fig, ax = plt.subplots(1, 5, figsize=(14, 3.5))
        ax = ax.ravel()
        for i in range(5):
            for j in [i, i + 5]:
                y = results[j, x]
                if i in [0, 1, 2]:
                    y[y == 0] = np.nan  # dont show zero loss_funcs values
                ax[i].plot(x, y, marker='.', label=s[j])
            ax[i].set_title(t[i])
            ax[i].legend()
            ax[i].set_ylabel(f) if i == 0 else None  # add filename
        fig.tight_layout()
        fig.savefig(f.replace('.txt', '.png'), dpi=200)


def plot_results(start=0, stop=0, bucket='', id=()):  # from evaluate_utils.evaluate_utils import *; plot_results()
    """
    :param start:
    :param stop:
    :param bucket:
    :param id:
    :return:
    """
    # Plot training 'results*.txt' as seen in https://github.com/ultralytics/yolov3#training
    fig, ax = plt.subplots(2, 5, figsize=(12, 6))
    ax = ax.ravel()
    s = ['GIoU', 'Objectness', 'Classification', 'Precision', 'Recall',
         'val GIoU', 'val Objectness', 'val Classification', 'mAP@0.5', 'F1']
    if bucket:
        os.system('rm -rf storage.googleapis.com')
        files = ['https://storage.googleapis.com/%s/results%g.txt' % (bucket, x) for x in id]
    else:
        files = glob.glob('results*.txt') + glob.glob('../../Downloads/results*.txt')
    for f in sorted(files):
        try:
            results = np.loadtxt(f, usecols=[2, 3, 4, 8, 9, 12, 13, 14, 10, 11], ndmin=2).T
            n = results.shape[1]  # number of rows
            x = range(start, min(stop, n) if stop else n)
            for i in range(10):
                y = results[i, x]
                if i in [0, 1, 2, 5, 6, 7]:
                    y[y == 0] = np.nan  # dont show zero loss_funcs values
                    # y /= y[0]  # normalize
                ax[i].plot(x, y, marker='.', label=Path(f).stem, linewidth=2, markersize=8)
                ax[i].set_title(s[i])
                if i in [5, 6, 7]:  # share train and val loss_funcs y axes
                    ax[i].get_shared_y_axes().join(ax[i], ax[i - 5])
        except:
            print('Warning: Plotting error for %s, skipping file' % f)

    fig.tight_layout()
    ax[1].legend()
    fig.savefig('results.png', dpi=200)


class GHMC(nn.Module):
    """
    GHM Classification Loss.
    Details of the theorem can be viewed in the paper
    "Gradient Harmonized Single-stage Detector".
    https://arxiv.org/abs/1811.05181
    Args:
        bins (int): Number of the unit regions for distribution calculation.
        momentum (float): The parameter for moving average.
        use_sigmoid (bool): Can only be true for BCE based loss now.
        loss_weight (float): The weight of the total GHM-C loss.
    """

    def __init__(
            self,
            bins=10,
            momentum=0,
            use_sigmoid=True,
            loss_weight=1.0):
        """
        :param bins:
        :param momentum:
        :param use_sigmoid:
        :param loss_weight:
        """
        super(GHMC, self).__init__()

        self.bins = bins
        self.momentum = momentum
        self.edges = torch.arange(bins + 1).float().cuda() / bins
        self.edges[-1] += 1e-6

        if momentum > 0:
            self.acc_sum = torch.zeros(bins).cuda()

        self.use_sigmoid = use_sigmoid
        if not self.use_sigmoid:
            raise NotImplementedError
        self.loss_weight = loss_weight

    def forward(self, pred, target, label_weight, *args, **kwargs):
        """Calculate the GHM-C loss.
        Args:
            pred (float tensor of size [batch_num, class_num]):
                The direct prediction of classification fc layer.
            target (float tensor of size [batch_num, class_num]):
                Binary class target for each sample.
            label_weight (float tensor of size [batch_num, class_num]):
                the value is 1 if the sample is valid and 0 if ignored.
        Returns:
            The gradient harmonized loss.
        """
        # the target should be binary class label
        if pred.dim() != target.dim():
            target, label_weight = _expand_binary_labels(
                target, label_weight, pred.size(-1))

        target, label_weight = target.float(), label_weight.float()
        edges = self.edges
        mmt = self.momentum
        weights = torch.zeros_like(pred, dtype=torch.float32).cuda()

        # gradient length
        g = torch.abs(pred.sigmoid().detach() - target)

        valid = label_weight > 0
        try:
            tot = max(valid.float().sum().item(), 1.0)
        except Exception as e:
            print(e)

        n = 0  # n valid bins
        for i in range(self.bins):
            inds = (g >= edges[i]) & (g < edges[i + 1]) & valid
            num_in_bin = inds.sum().item()
            if num_in_bin > 0:
                if mmt > 0:
                    self.acc_sum[i] = mmt * self.acc_sum[i] \
                                      + (1 - mmt) * num_in_bin
                    weights[inds] = tot / self.acc_sum[i]
                else:
                    weights[inds] = tot / num_in_bin
                n += 1
        if n > 0:
            weights = weights / n

        loss = F.binary_cross_entropy_with_logits(pred, target, weights, reduction='sum') / tot

        return loss * self.loss_weight


## ---------- File operations
def cmpTwoVideos(src_root, dst_root,
                 ext=".mp4", flag1="old", flag2="new"):
    """
    :param src_root:
    :param dst_root:
    :param ext:
    :param flag1:
    :param flag2:
    :return:
    """
    if not os.path.isdir(src_root):
        print("[Err]: invalid src root!")
        return

    parent_dir = os.path.abspath(os.path.join(src_root, ".."))
    tmp_dir = parent_dir + "/tmp"
    tmp_dir = os.path.abspath(tmp_dir)
    if not os.path.isdir(tmp_dir):
        os.makedirs(tmp_dir)

    if dst_root is None:
        # os.makedirs(dst_root)
        # print("{:s} made.".format(dst_root))
        dst_root = src_root

    videos1 = [src_root + "/" + x for x in os.listdir(src_root) if x.endswith(ext) and flag1 in x]
    videos2 = [src_root + "/" + x for x in os.listdir(src_root) if x.endswith(ext) and flag2 in x]

    # assert len(videos1) == len(videos2)

    videos1.sort()
    videos2.sort()

    for vid1_path in videos1:
        vid2_path = vid1_path.replace(flag1, flag2)
        if not (os.path.isfile(vid1_path) and os.path.isfile(vid2_path)):
            print("[Warning]: invalid file path.")
            continue

        cmd_str = "rm -rf {:s}/*.jpg".format(tmp_dir)
        print(cmd_str)
        os.system(cmd_str)

        vid1_name = os.path.split(vid1_path)[-1]
        vid_name = vid1_name.replace(flag1, "")

        ## ----- 读取视频
        cap1 = cv2.VideoCapture(vid1_path)
        cap2 = cv2.VideoCapture(vid2_path)

        # 获取视频所有帧数
        FRAME_NUM1 = int(cap1.get(cv2.CAP_PROP_FRAME_COUNT))
        FRAME_NUM2 = int(cap2.get(cv2.CAP_PROP_FRAME_COUNT))
        assert FRAME_NUM1 == FRAME_NUM2
        print('Total {:d} frames'.format(FRAME_NUM1))

        if FRAME_NUM1 == 0:
            break

        for i in range(0, FRAME_NUM1):
            success1, frame1 = cap1.read()
            success2, frame2 = cap2.read()

            if not (success1 and success2):  # 判断当前帧是否存在
                print("[Warning]: read frame-pair failed @frame{:d}!".format(i))
                break

            assert frame1.shape == frame2.shape

            ## ----- 设置输出帧
            H, W, C = frame1.shape
            if W >= H:
                res = np.zeros((H * 2, W, 3), dtype=np.uint8)
                res[:H, :, :] = frame1
                res[H:2 * H, :, :] = frame2
            else:
                res = np.zeros((H, W * 2, 3), dtype=np.uint8)
                res[:, :W, :] = frame1
                res[:, W:2 * W, :] = frame2

            ## ----- 输出到tmp目录
            res_sv_path = tmp_dir + "/{:04d}.jpg".format(i)
            cv2.imwrite(res_sv_path, res)
            print("{:s} saved.".format(res_sv_path))

        ## ---------- 输出视频结果
        vid_sv_path = dst_root + "/" + vid_name[:-len(ext)] + "cmp" + ext
        cmd_str = 'ffmpeg -f image2 -r 6 -i {:s}/%04d.jpg -b 5000k -c:v mpeg4 {}' \
            .format(tmp_dir, vid_sv_path)
        print(cmd_str)
        os.system(cmd_str)


if __name__ == "__main__":
    cmpTwoVideos(src_root="../output/", dst_root=None,
                 flag1="fair", flag2="byte")
