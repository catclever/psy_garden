#!/usr/bin/env python3
"""
Stem-first root_anchor calibrator for Psy Garden 2.5D plant assets.

Resolves Category-B plants whose outer drooping leaves or blossoms extend below
or laterally away from the central stem base by prioritizing:
  B1. Woody stem base in layer_red within the central corridor.
  B2/B3. Central crown base density threshold in the combined alpha mask.
"""

import math
import statistics


def _is_woody_pixel(r: int, g: int, b: int, a: int) -> bool:
    return a > 38 and r >= g - 12 and (r - g) < 85 and b < r + 10


def _percentile_75(sorted_vals: list) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = 0.75 * (len(sorted_vals) - 1)
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f]))


def calibrate_root_anchor(red_img, green_img, blue_img=None) -> dict:
    """Calibrate stem-first ground anchor and footprint metrics for layer RGBA images."""
    ref_img = red_img or green_img or blue_img
    if ref_img is None:
        return {
            "root_x": 0.0,
            "root_y": 0,
            "root_anchor": [0.5, 1.0],
            "root_width": 8,
            "root_ratio": 0.15,
            "ground_ellipse": [8, 4],
        }

    W, H = ref_img.size
    if W <= 0 or H <= 0:
        return {
            "root_x": 0.0,
            "root_y": 0,
            "root_anchor": [0.5, 1.0],
            "root_width": 8,
            "root_ratio": 0.15,
            "ground_ellipse": [8, 4],
        }

    def _to_rgba_data(img):
        if img is None:
            return None
        rgba = img.convert("RGBA") if img.mode != "RGBA" else img
        if rgba.size != (W, H):
            raise ValueError(f"Expected image size {(W, H)}, got {rgba.size}")
        return list(rgba.getdata())

    red_data = _to_rgba_data(red_img)
    green_data = _to_rgba_data(green_img)
    blue_data = _to_rgba_data(blue_img)

    opaque_rows = [[] for _ in range(H)]
    min_x = W
    max_x = -1
    top_y = H
    bottom_y = -1

    for y in range(H):
        row_off = y * W
        row_xs = opaque_rows[y]
        for x in range(W):
            idx = row_off + x
            a_r = red_data[idx][3] if red_data is not None else 0
            a_g = green_data[idx][3] if green_data is not None else 0
            a_b = blue_data[idx][3] if blue_data is not None else 0
            if a_r > 38 or a_g > 38 or a_b > 38:
                row_xs.append(x)
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < top_y:
                    top_y = y
                if y > bottom_y:
                    bottom_y = y

    if bottom_y == -1 or max_x < min_x:
        fallback_w = max(8, min(int(W * 0.15), int(W * 0.65)))
        return {
            "root_x": round(W / 2.0, 2),
            "root_y": max(0, H - 1),
            "root_anchor": [0.5, 1.0],
            "root_width": fallback_w,
            "root_ratio": round(fallback_w / W, 3),
            "ground_ellipse": [fallback_w, max(4, int(round(fallback_w * 0.55)))],
        }

    vis_w = max(1, max_x - min_x + 1)
    vis_h = max(1, bottom_y - top_y + 1)

    anchor_x = None
    anchor_y = None
    stem_w = 0
    b1_matched = False

    # Priority B1: Woody Stem Base in red_img
    if red_data is not None:
        b1_x_min = min_x + 0.16 * vis_w
        b1_x_max = max_x - 0.16 * vis_w
        b1_y_min = top_y + 0.48 * vis_h
        y_scan_start = max(top_y, int(math.floor(b1_y_min)))

        woody_rows = {}
        for y in range(y_scan_start, bottom_y + 1):
            if y < b1_y_min:
                continue
            row_off = y * W
            xs_in_row = []
            x_lo = max(min_x, int(math.ceil(b1_x_min)))
            x_hi = min(max_x, int(math.floor(b1_x_max)))
            for x in range(x_lo, x_hi + 1):
                r, g, b, a = red_data[row_off + x]
                if _is_woody_pixel(r, g, b, a):
                    xs_in_row.append(x)
            if xs_in_row:
                woody_rows[y] = xs_in_row

        total_woody = sum(len(xs) for xs in woody_rows.values())
        qualifying_rows = sum(1 for xs in woody_rows.values() if len(xs) >= 2)

        if total_woody >= 25 and qualifying_rows >= 5:
            sorted_ys = sorted(woody_rows.keys())
            clusters = []
            current_cluster = [sorted_ys[0]]
            for y in sorted_ys[1:]:
                if (y - current_cluster[-1] - 1) <= 6:
                    current_cluster.append(y)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [y]
            clusters.append(current_cluster)

            woody_bottom_y = None
            winning_cluster = None
            for cluster in reversed(clusters):
                cluster_px = sum(len(woody_rows[y]) for y in cluster)
                if cluster_px >= 4:
                    woody_bottom_y = cluster[-1]
                    winning_cluster = cluster
                    break

            if (
                woody_bottom_y is not None
                and woody_bottom_y >= top_y + 0.62 * vis_h
                and 0 <= (bottom_y - woody_bottom_y) <= 0.32 * vis_h
            ):
                anchor_y = woody_bottom_y
                rough_xs = [
                    x
                    for y in winning_cluster
                    if y >= anchor_y - 7
                    for x in woody_rows[y]
                ]
                if not rough_xs:
                    rough_xs = [x for y in winning_cluster for x in woody_rows[y]]
                rough_woody_x = sum(rough_xs) / len(rough_xs)

                win_lo = rough_woody_x - 0.18 * vis_w
                win_hi = rough_woody_x + 0.18 * vis_w
                x_start = max(0, int(math.ceil(win_lo)))
                x_end = min(W - 1, int(math.floor(win_hi)))

                stem_xs = []
                row_spans = []
                for y in range(max(0, anchor_y - 7), anchor_y + 1):
                    row_off = y * W
                    row_hit = []
                    for x in range(x_start, x_end + 1):
                        idx = row_off + x
                        r, g, b, a = red_data[idx]
                        is_woody = _is_woody_pixel(r, g, b, a)
                        is_green_stem = green_data is not None and green_data[idx][3] > 38
                        if is_woody or is_green_stem:
                            row_hit.append(x)
                            stem_xs.append(x)
                    if row_hit:
                        row_spans.append(row_hit[-1] - row_hit[0] + 1)

                anchor_x = (sum(stem_xs) / len(stem_xs)) if stem_xs else rough_woody_x
                stem_w = max(row_spans) if row_spans else 0
                b1_matched = True

    # Fallback B2/B3: Central Crown Base
    if not b1_matched:
        y_50 = top_y + 0.50 * vis_h
        lower_xs = []
        for y in range(max(top_y, int(math.floor(y_50))), bottom_y + 1):
            if y >= y_50 and opaque_rows[y]:
                lower_xs.extend(opaque_rows[y])
        if not lower_xs:
            for y in range(top_y, bottom_y + 1):
                if opaque_rows[y]:
                    lower_xs.extend(opaque_rows[y])

        cx = float(statistics.median(lower_xs))
        corr_lo = cx - 0.20 * vis_w
        corr_hi = cx + 0.20 * vis_w

        y_55 = top_y + 0.55 * vis_h
        y_88 = top_y + 0.88 * vis_h
        core_cnt = {}
        for y in range(max(top_y, int(math.floor(y_55))), bottom_y + 1):
            if y < y_55:
                continue
            core_cnt[y] = sum(1 for x in opaque_rows[y] if corr_lo <= x <= corr_hi)

        p_vals = sorted(cnt for y, cnt in core_cnt.items() if y <= y_88 and cnt > 0)
        if not p_vals:
            p_vals = sorted(cnt for cnt in core_cnt.values() if cnt > 0)

        ref_cnt = _percentile_75(p_vals)
        thresh = max(3.0, 0.22 * ref_cnt)

        qualifying_crown_ys = [y for y, cnt in core_cnt.items() if cnt >= thresh]
        if qualifying_crown_ys:
            crown_y = max(qualifying_crown_ys)
        else:
            non_zero_ys = [y for y, cnt in core_cnt.items() if cnt > 0]
            crown_y = max(non_zero_ys) if non_zero_ys else bottom_y

        anchor_y = max(crown_y, int(top_y + 0.68 * vis_h))
        anchor_y = min(anchor_y, bottom_y)

        corr_xs = [
            x
            for y in range(max(0, anchor_y - 7), anchor_y + 1)
            for x in opaque_rows[y]
            if corr_lo <= x <= corr_hi
        ]
        if not corr_xs:
            corr_xs = [
                x
                for y in range(max(0, bottom_y - 7), bottom_y + 1)
                for x in opaque_rows[y]
            ]
        anchor_x = (sum(corr_xs) / len(corr_xs)) if corr_xs else cx

        half_w = max(0.25 * vis_w, 8.0)
        span_lo = anchor_x - half_w
        span_hi = anchor_x + half_w
        row_spans = []
        for y in range(max(0, anchor_y - 7), anchor_y + 1):
            row_hit = [x for x in opaque_rows[y] if span_lo <= x <= span_hi]
            if row_hit:
                row_spans.append(row_hit[-1] - row_hit[0] + 1)
        if not row_spans:
            for y in range(max(0, bottom_y - 7), bottom_y + 1):
                if opaque_rows[y]:
                    row_spans.append(opaque_rows[y][-1] - opaque_rows[y][0] + 1)
        stem_w = max(row_spans) if row_spans else max(8, int(W * 0.15))

    root_width = max(8, min(int(stem_w), int(W * 0.65)))
    root_ratio = round(root_width / W, 3)
    ground_ellipse = [root_width, max(4, int(round(root_width * 0.55)))]

    return {
        "root_x": round(float(anchor_x), 2),
        "root_y": int(anchor_y),
        "root_anchor": [round(float(anchor_x) / W, 4), round(float(anchor_y) / H, 4)],
        "root_width": int(root_width),
        "root_ratio": root_ratio,
        "ground_ellipse": ground_ellipse,
    }
