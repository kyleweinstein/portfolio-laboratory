export type PublicationChartPoint = {
  date: string;
  returnPercent: number;
  benchmarkReturnPercent: number | null;
};

export type PublicationChartSeries =
  | "returnPercent"
  | "benchmarkReturnPercent";

export function publicationChartSegments(
  points: PublicationChartPoint[],
  series: PublicationChartSeries,
  {
    width,
    height,
    padding,
    minimum,
    maximum,
  }: {
    width: number;
    height: number;
    padding: number;
    minimum: number;
    maximum: number;
  },
): string[] {
  const range = Math.max(1, maximum - minimum);
  const segments: string[] = [];
  let current: string[] = [];
  const finish = () => {
    if (current.length) segments.push(current.join(" "));
    current = [];
  };

  points.forEach((point, index) => {
    const value = point[series];
    if (value === null || !Number.isFinite(value)) {
      finish();
      return;
    }
    const x = padding +
      (index / Math.max(1, points.length - 1)) * (width - padding * 2);
    const y = padding +
      ((maximum - value) / range) * (height - padding * 2);
    current.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  finish();
  return segments;
}
