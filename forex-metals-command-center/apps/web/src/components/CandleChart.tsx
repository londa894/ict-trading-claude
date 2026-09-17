"use client";

import type { ChartCandle, ChartTimeframe } from "@fmcc/shared-types";
import type { IChartApi, IPriceLine, ISeriesApi, ISeriesMarkersPluginApi, Time, UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { DOWN_COLOR, toRendererData, UP_COLOR } from "@/lib/candles";
import { formatChartTime, formatTick, type DisplayZone } from "@/lib/sessions";
import type { Overlay } from "@/lib/structure";

type LightweightCharts = typeof import("lightweight-charts");

type ChartHandles = {
  lib: LightweightCharts;
  chart: IChartApi;
  bars: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
  markers: ISeriesMarkersPluginApi<Time>;
  segments: ISeriesApi<"Line">[];
  priceLines: IPriceLine[];
};

/**
 * TradingView Lightweight Charts used strictly as a renderer: it receives validated candles and a
 * pre-verified overlay from the backend and has no data path of its own. Candle times are UTC; `timeZone` only
 * changes how axis and crosshair labels are formatted.
 */
export function CandleChart({
  candles,
  timeframe,
  overlay = null,
  timeZone = "UTC",
}: {
  candles: ChartCandle[];
  timeframe: ChartTimeframe;
  overlay?: Overlay | null;
  timeZone?: DisplayZone;
}) {
  const container = useRef<HTMLDivElement>(null);
  const handles = useRef<ChartHandles | null>(null);
  const fittedFor = useRef<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let disposed = false;
    void import("lightweight-charts").then((lib) => {
      if (disposed || !container.current) return;
      const chart = lib.createChart(container.current, {
        autoSize: true,
        layout: { background: { type: lib.ColorType.Solid, color: "#0d1117" }, textColor: "#8b949e" },
        grid: { vertLines: { color: "#161b22" }, horzLines: { color: "#161b22" } },
        rightPriceScale: { borderColor: "#30363d" },
        timeScale: { borderColor: "#30363d", timeVisible: true, secondsVisible: false },
      });
      const bars = chart.addSeries(lib.CandlestickSeries, {
        upColor: UP_COLOR,
        downColor: DOWN_COLOR,
        wickUpColor: UP_COLOR,
        wickDownColor: DOWN_COLOR,
        borderVisible: false,
      });
      const volume = chart.addSeries(lib.HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" } });
      chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      const markers = lib.createSeriesMarkers(bars, []);
      handles.current = { lib, chart, bars, volume, markers, segments: [], priceLines: [] };
      setReady(true);
    });
    return () => {
      disposed = true;
      handles.current?.chart.remove();
      handles.current = null;
    };
  }, []);

  useEffect(() => {
    const h = handles.current;
    if (!ready || !h) return;
    const { bars, volumes } = toRendererData(candles);
    h.bars.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
    h.volume.setData(volumes.map((v) => ({ ...v, time: v.time as UTCTimestamp })));
    if (fittedFor.current !== timeframe) {
      h.chart.timeScale().fitContent();
      fittedFor.current = timeframe;
    }
  }, [ready, candles, timeframe]);

  useEffect(() => {
    const h = handles.current;
    if (!ready || !h) return;
    h.chart.applyOptions({
      localization: { timeFormatter: (t: Time) => formatChartTime(Number(t), timeZone) },
      timeScale: { tickMarkFormatter: (t: Time, type: number) => formatTick(Number(t), type, timeZone) },
    });
  }, [ready, timeZone]);

  useEffect(() => {
    const h = handles.current;
    if (!ready || !h) return;
    for (const s of h.segments) h.chart.removeSeries(s);
    for (const p of h.priceLines) h.bars.removePriceLine(p);
    h.segments = [];
    h.priceLines = [];
    h.markers.setMarkers(
      (overlay?.markers ?? []).map((m) => ({ ...m, time: m.time as UTCTimestamp })),
    );
    for (const seg of overlay?.segments ?? []) {
      const line = h.chart.addSeries(h.lib.LineSeries, {
        color: seg.color,
        lineWidth: 1,
        lineStyle: seg.dashed ? h.lib.LineStyle.Dashed : h.lib.LineStyle.Solid,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null,
      });
      line.setData([
        { time: seg.from as UTCTimestamp, value: seg.price },
        { time: seg.to as UTCTimestamp, value: seg.price },
      ]);
      h.segments.push(line);
    }
    for (const pl of overlay?.priceLines ?? []) {
      h.priceLines.push(
        h.bars.createPriceLine({
          price: pl.price,
          color: pl.color,
          lineWidth: 1,
          lineStyle:
            pl.style === "solid"
              ? h.lib.LineStyle.Solid
              : pl.style === "dashed"
                ? h.lib.LineStyle.Dashed
                : h.lib.LineStyle.Dotted,
          axisLabelVisible: true,
          title: pl.title,
        }),
      );
    }
  }, [ready, overlay, candles]);

  return <div ref={container} className="chart-canvas" data-testid="candle-chart" />;
}
