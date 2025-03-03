# encoding=utf-8

import lap
import numpy as np
import numpy as np
import scipy
from cython_bbox import bbox_overlaps as bbox_ious
from scipy.spatial.distance import cdist
from yolox.tracker import kalman_filter


def merge_matches(m1, m2, shape):
    """
    :param m1:
    :param m2:
    :param shape:
    :return:
    """
    O, P, Q = shape
    m1 = np.asarray(m1)
    m2 = np.asarray(m2)

    M1 = scipy.sparse.coo_matrix((np.ones(len(m1)), (m1[:, 0], m1[:, 1])), shape=(O, P))
    M2 = scipy.sparse.coo_matrix((np.ones(len(m2)), (m2[:, 0], m2[:, 1])), shape=(P, Q))

    mask = M1 * M2
    match = mask.nonzero()
    match = list(zip(match[0], match[1]))
    unmatched_O = tuple(set(range(O)) - set([i for i, j in match]))
    unmatched_Q = tuple(set(range(Q)) - set([j for i, j in match]))

    return match, unmatched_O, unmatched_Q


def _indices_to_matches(cost_matrix, indices, thresh):
    """
    :param cost_matrix:
    :param indices:
    :param thresh:
    :return:
    """
    matched_cost = cost_matrix[tuple(zip(*indices))]
    matched_mask = (matched_cost <= thresh)

    matches = indices[matched_mask]
    unmatched_a = tuple(set(range(cost_matrix.shape[0])) - set(matches[:, 0]))
    unmatched_b = tuple(set(range(cost_matrix.shape[1])) - set(matches[:, 1]))

    return matches, unmatched_a, unmatched_b


def linear_assignment(cost_matrix, thresh):
    """
    :param cost_matrix:
    :param thresh:
    :return:
    """
    if cost_matrix.size == 0:
        return np.empty((0, 2), dtype=int), tuple(range(cost_matrix.shape[0])), tuple(range(cost_matrix.shape[1]))

    matches, unmatched_a, unmatched_b = [], [], []
    cost, x, y = lap.lapjv(cost_matrix, extend_cost=True, cost_limit=thresh)

    for ix, mx in enumerate(x):
        if mx >= 0:
            matches.append([ix, mx])

    unmatched_a = np.where(x < 0)[0]
    unmatched_b = np.where(y < 0)[0]
    matches = np.asarray(matches)

    return matches, unmatched_a, unmatched_b


def ious(a_tlbrs, b_tlbrs):
    """
    Compute cost based on IoU
    :type a_tlbrs: list[tlbr] | np.ndarray
    :type b_tlbrs: list[tlbr] | np.ndarray
    :rtype ious np.ndarray
    """
    ious = np.zeros((len(a_tlbrs), len(b_tlbrs)), dtype=np.float)
    if ious.size == 0:
        return ious

    ious = bbox_ious(
        np.ascontiguousarray(a_tlbrs, dtype=np.float),
        np.ascontiguousarray(b_tlbrs, dtype=np.float)
    )

    return ious


def iou_distance(a_tracks, b_tracks):
    """
    Compute cost based on IoU
    :type a_tracks: list[Track]
    :type b_tracks: list[Track]
    :rtype cost_matrix np.ndarray
    """
    if (len(a_tracks) > 0 and isinstance(a_tracks[0], np.ndarray)) or (
            len(b_tracks) > 0 and isinstance(b_tracks[0], np.ndarray)):
        a_tlbrs = a_tracks
        b_tlbrs = b_tracks
    else:
        a_tlbrs = [track.tlbr for track in a_tracks]
        b_tlbrs = [track.tlbr for track in b_tracks]

    _ious = ious(a_tlbrs, b_tlbrs)
    cost_matrix = 1 - _ious

    return cost_matrix


def v_iou_distance(a_tracks, b_tracks):
    """
    Compute cost based on IoU
    :type a_tracks: list[STrack]
    :type b_tracks: list[STrack]
    :rtype cost_matrix np.ndarray
    """
    if (len(a_tracks) > 0 and isinstance(a_tracks[0], np.ndarray)) or (
            len(b_tracks) > 0 and isinstance(b_tracks[0], np.ndarray)):
        a_tlbrs = a_tracks
        b_tlbrs = b_tracks
    else:
        a_tlbrs = [track.tlwh_to_tlbr(track.pred_bbox) for track in a_tracks]
        b_tlbrs = [track.tlwh_to_tlbr(track.pred_bbox) for track in b_tracks]

    _ious = ious(a_tlbrs, b_tlbrs)
    cost_matrix = 1 - _ious

    return cost_matrix


def embedding_distance(tracks, detections, metric='cosine'):
    """
    :param tracks: list[STrack]
    :param detections: list[BaseTrack]
    :param metric:
    :return: cost_matrix np.ndarray
    """
    cost_matrix = np.zeros((len(tracks), len(detections)), dtype=np.float)
    if cost_matrix.size == 0:
        return cost_matrix
    det_features = np.asarray([track.curr_feat for track in detections], dtype=np.float)
    # for i, track in enumerate(tracks):
    # cost_matrix[i, :] = np.maximum(0.0, cdist(track.smooth_feat.reshape(1,-1), det_features, metric))
    track_features = np.asarray([track.smooth_feat for track in tracks], dtype=np.float)
    cost_matrix = np.maximum(0.0, cdist(track_features, det_features, metric))  # Nomalized features
    return cost_matrix


def gate_cost_matrix(kf, cost_matrix, tracks, detections, only_position=False):
    """
    :param kf:
    :param cost_matrix:
    :param tracks:
    :param detections:
    :param only_position:
    :return:
    """
    if cost_matrix.size == 0:
        return cost_matrix
    gating_dim = 2 if only_position else 4
    gating_threshold = kalman_filter.chi2inv95[gating_dim]
    measurements = np.asarray([det.to_xyah() for det in detections])
    for row, track in enumerate(tracks):
        gating_distance = kf.gating_distance(
            track.mean, track.covariance, measurements, only_position)
        cost_matrix[row, gating_distance > gating_threshold] = np.inf
    return cost_matrix


def fuse_motion(kf, cost_matrix, tracks, detections, only_position=False, lambda_=0.98):
    """
    :param kf:
    :param cost_matrix:
    :param tracks:
    :param detections:
    :param only_position:
    :param lambda_:
    :return:
    """
    if cost_matrix.size == 0:
        return cost_matrix
    gating_dim = 2 if only_position else 4
    gating_threshold = kalman_filter.chi2inv95[gating_dim]
    measurements = np.asarray([det.to_xyah() for det in detections])
    for row, track in enumerate(tracks):
        gating_distance = kf.gating_distance(
            track.mean, track.covariance, measurements, only_position, metric='maha')
        cost_matrix[row, gating_distance > gating_threshold] = np.inf
        cost_matrix[row] = lambda_ * cost_matrix[row] + (1 - lambda_) * gating_distance
    return cost_matrix


def fuse_iou(cost_matrix, tracks, detections):
    """
    :param cost_matrix:
    :param tracks:
    :param detections:
    :return:
    """
    if cost_matrix.size == 0:
        return cost_matrix
    reid_sim = 1 - cost_matrix
    iou_dist = iou_distance(tracks, detections)
    iou_sim = 1 - iou_dist
    fuse_sim = reid_sim * (1 + iou_sim) / 2
    det_scores = np.array([det.score for det in detections])
    det_scores = np.expand_dims(det_scores, axis=0).repeat(cost_matrix.shape[0], axis=0)
    # fuse_sim = fuse_sim * (1 + det_scores) / 2
    fuse_cost = 1 - fuse_sim
    return fuse_cost


def fuse_score(cost_matrix, detections):
    """
    :param cost_matrix:
    :param detections:
    :return:
    """
    if cost_matrix.size == 0:
        return cost_matrix

    iou_sim = 1.0 - cost_matrix
    det_scores = np.array([det.score for det in detections])
    det_scores = np.expand_dims(det_scores, axis=0).repeat(cost_matrix.shape[0], axis=0)
    fuse_sim = iou_sim * det_scores
    fuse_cost = 1.0 - fuse_sim

    return fuse_cost


def fuse_costs(cost_mat1, cost_mat2):
    """
    :param cost_mat1:
    :param cost_mat2:
    :return:
    """
    if cost_mat1.size == 0:
        return cost_mat1
    if cost_mat2.size == 0:
        return cost_mat2

    sim1 = 1.0 - cost_mat1
    sim2 = 1.0 - cost_mat2

    fuse_sim = sim1 * sim2
    fuse_cost = 1.0 - fuse_sim

    return fuse_cost


def weight_sum_costs(cost_mat1, cost_mat2, alpha=0.5):
    """
    :param cost_mat1:
    :param cost_mat2:
    :param alpha:
    :return:
    """
    if cost_mat1.size == 0:
        return cost_mat1
    if cost_mat2.size == 0:
        return cost_mat2

    sim1 = 1.0 - cost_mat1
    sim2 = 1.0 - cost_mat2

    fuse_sim = sim1 * alpha + (1.0 - alpha) * sim2
    fuse_cost = 1.0 - fuse_sim

    return fuse_cost