function statsPage() {
    return {
        init() {
            this.loadBatchData();
            this.loadTagsData();
            this.loadQualityData();
        },

        async loadBatchData() {
            const response = await fetch('/api/stats/batches');
            const data = await response.json();
            this.renderChart('batchChart', 'pie', data, 'Videos per Batch');
        },

        async loadTagsData() {
            const response = await fetch('/api/stats/tags');
            const data = await response.json();
            this.renderChart('tagsChart', 'bar', data, 'Top 20 Tags');
        },

        async loadQualityData() {
            const response = await fetch('/api/stats/quality');
            const data = await response.json();
            this.renderChart('qualityChart', 'doughnut', data, 'Videos by Quality');
        },

        renderChart(elementId, type, chartData, label) {
            const ctx = document.getElementById(elementId).getContext('2d');
            new Chart(ctx, {
                type: type,
                data: {
                    labels: chartData.map(d => d.label),
                    datasets: [{
                        label: label,
                        data: chartData.map(d => d.value),
                        backgroundColor: this.generateColors(chartData.length),
                        borderColor: '#444',
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            position: type === 'bar' ? 'top' : 'right',
                            labels: { color: '#eee' }
                        }
                    },
                    scales: {
                        y: {
                           ticks: { color: '#eee' }
                        },
                        x: {
                           ticks: { color: '#eee' }
                        }
                    }
                }
            });
        },
        
        generateColors(numColors) {
            const colors = [];
            for (let i = 0; i < numColors; i++) {
                const hue = (i * 360) / numColors;
                colors.push(`hsla(${hue}, 70%, 60%, 0.8)`);
            }
            return colors;
        }
    }
}

// Since AlpineJS is not on this page, we call it manually.
document.addEventListener('DOMContentLoaded', () => {
    const stats = statsPage();
    stats.init();
});
