echo "=== MONITORING NAMESPACE STATUS ==="

echo "1. All pods in monitoring:"
kubectl get pods -n monitoring

echo "2. All deployments in monitoring:"
kubectl get deployments -n monitoring

echo "3. Prometheus CRD:"
kubectl get prometheus -n monitoring

echo "4. ServiceMonitor processing status:"
kubectl get servicemonitors -n monitoring

echo "5. Check if operator-related resources exist:"
kubectl get pods -n monitoring -l "app.kubernetes.io/name=prometheus-operator" 2>/dev/null || echo "No operator pods found"
kubectl get deployments -n monitoring -l "app.kubernetes.io/name=prometheus-operator" 2>/dev/null || echo "No operator deployment found"