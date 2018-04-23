import django_filters

from django.db.models import Q
from django.db import transaction
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.shortcuts import get_object_or_404

from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import filters

from apps.user.models import Referral, User, ReferralFeedback
from apps.payment.models import PaymentTransaction, PaymentProcessor

from api.permissions import ObjectOwnerPermission, SufficientFundsPermission
from api.paginations import ReferralPagination
from api.serializers.referral import ReferralSerializer, ReferralFeedbackSer
from django.views.decorators.csrf import csrf_exempt


class MyRangeFilter(django_filters.filters.RangeFilter):
    # field_class = django_filters.fields.RangeField   # not supported
    def filter(self, qs, value):
        class Value(object):
            def __init__(self, value):
                self.start, self.stop = value.split(',')

        return super(MyRangeFilter, self).filter(self, qs, Value(value))

class ReferralFilter(filters.FilterSet):

    industry = django_filters.CharFilter(name="industry")
    address = django_filters.CharFilter(name="address", lookup_expr='icontains')
    income = django_filters.NumberFilter(name="income", lookup_expr='gte')
    user = django_filters.NumberFilter(name="user_id")
    score = MyRangeFilter(name="score")
    price = MyRangeFilter(name="price")
    advisor_industry = django_filters.CharFilter(name="user__industry")
    advisor_city = django_filters.CharFilter(name="user__industry", lookup_expr='icontains')
    advisor_province = django_filters.CharFilter(name="user__industry", lookup_expr='icontains')

    class Meta:
        model = Referral
        fields = ['address', 'income', 'price', 'industry', 'user', 'score', 'price', 'advisor_city',
                  'advisor_industry', 'advisor_province']


class ReferralListAPIView(generics.ListAPIView):
    permission_classes = []

    serializer_class = ReferralSerializer
    pagination_class = ReferralPagination
    filter_backends = (filters.DjangoFilterBackend, filters.OrderingFilter)
    filter_class = ReferralFilter
    ordering_fields = ('income', 'price')

    def get_queryset(self):
        score_min = self.request.GET.get('score_min', None)
        score_max = self.request.GET.get('score_max', None)
        price_min = self.request.GET.get('price_min', None)
        price_max = self.request.GET.get('price_max', None)
        advisor_score_min = self.request.GET.get('advisor_score_min', None)
        advisor_score_max = self.request.GET.get('advisor_score_max', None)

        filter_dict = {
            'user_bought': None,
            'cur_buy_request': None,
        }

        if score_min and score_max:
            filter_dict['score__range'] = (float(score_min), float(score_max))
        elif score_min:
            filter_dict['score__gte'] = float(score_min)
        elif score_max:
            filter_dict['score__lte'] = float(score_max)

        if price_min and price_max:
            filter_dict['price__range'] = (float(price_min), float(price_max))
        elif price_min:
            filter_dict['price__gte'] = float(price_min)
        elif price_max:
            filter_dict['price__lte'] = float(price_max)

        if advisor_score_min and advisor_score_max:
            filter_dict['user__score__range'] = (float(advisor_score_min), float(advisor_score_max))
        elif advisor_score_min:
            filter_dict['user__score__gte'] = float(advisor_score_min)
        elif advisor_score_max:
            filter_dict['user__score__lte'] = float(advisor_score_max)

        return Referral.objects.filter(**filter_dict).exclude(user=self.request.user).all()


class SaveReferral(APIView):
    """ Request example:
    {
        "first_name":"john",
        "last_name":"doel",
        "email": "john@gmail.com",
        "phone": "093 888 88 88",
        "industry":"johnIndustry",
        "price": 88,
        "income":8
    }
    """
    permission_classes = []

    @csrf_exempt
    def post(self, request):
        data = request.data.copy()
        #Referral can be created only by Request user
        data['user'] = request.user.id
        serialized = ReferralSerializer(data=data)
        if serialized.is_valid():
            serialized.save()
            return Response(serialized.data, status=status.HTTP_201_CREATED)
        return Response(serialized.errors, status=status.HTTP_400_BAD_REQUEST)


class UpdateReferral(APIView):
    """ Request example:
    {
        "name":"john",
        "surname":"doel",
        "email": "john@gmail.com",
        "phone": "093 888 88 88",
        "industry":"johnIndustry",
        "cost": 88,
        "income":8,
        "users":[]

    }
    """
    permission_classes = [ObjectOwnerPermission]

    @receiver(post_save, sender=User)
    def send_notifications(sender, **kwargs):
        # queryset = Referral.objects.get(pk=request.data['id'])
        pass
    
    @csrf_exempt
    def post(self, request):
        data = request.data.copy()
        model = get_object_or_404(Referral, hash=data['hash'])
        serialized = ReferralSerializer(instance=model, partial=True, data=data)

        self.check_object_permissions(request, model)

        if serialized.is_valid():
            serialized.save()
            return Response(serialized.data, status=status.HTTP_201_CREATED)
        return Response(serialized.errors, status=status.HTTP_400_BAD_REQUEST)


class DeleteReferral(APIView):
    """ Request example:
    {
        "hash": 1234334423413
    }
    """
    permission_classes = [ObjectOwnerPermission]

    @csrf_exempt
    def post(self, request):
        model = get_object_or_404(Referral, hash=request.data['hash'])
        self.check_object_permissions(request, model)
        model.is_removed = 1
        model.save()
        return Response(status=status.HTTP_200_OK)


class FilteredReferrals(generics.ListAPIView):
    """ Request example:
    {
        "queryField": "cost",
        "value": 88,
        "page":1,
        "sort_by": "name"
    }
    """
    permission_classes = []
    serializer_class = ReferralSerializer

    @csrf_exempt
    def post(self, request):
        refs = Referral.objects.filter(user=request.user.id)

        data = request.data.copy()
        query_field, value, page, sort = data['queryField'], data['value'], data['page'], data['sort_by']

        sort_by = 'score'
        if sort:
            sort_by = sort

        if query_field and value:
            queryset = Referral.objects.filter(**{query_field: value}).order_by(sort_by)
            paginator = Paginator(queryset.values(), 20)

            for r in refs:
                for pr in paginator.object_list:
                    if r.uid != pr["uid"]:
                        pr["email"] = ""
                        pr["phone"] = ""

            try:
                referrals = paginator.page(page)
            except PageNotAnInteger:
                referrals = paginator.page(1)
            except EmptyPage:
                referrals = paginator.page(paginator.num_pages)
            return Response([dict(item) for item in referrals], status=status.HTTP_200_OK)
        else:
            return Response(status=status.HTTP_400_BAD_REQUEST)


class BoughtReferrals(generics.ListAPIView):
    permission_classes = []
    serializer_class = ReferralSerializer
    pagination_class = ReferralPagination
    filter_backends = (filters.DjangoFilterBackend,)
    filter_class = ReferralFilter

    def get_queryset(self):
        return Referral.objects.filter( Q(user_bought=self.request.user) |
                                       Q(cur_buy_request__user=self.request.user) ).all()


class MyReferrals(generics.ListAPIView):
    permission_classes = []
    serializer_class = ReferralSerializer
    pagination_class = ReferralPagination
    filter_backends = (filters.DjangoFilterBackend,)
    filter_class = ReferralFilter

    def get_queryset(self):
        return Referral.objects.filter(user=self.request.user).all()


class MyReferralsRequests(generics.ListAPIView):
    permission_classes = []
    serializer_class = ReferralSerializer

    def paginate_queryset(self, queryset, view=None):
        return None

    def get_queryset(self):
        return Referral.objects.exclude(cur_buy_request=None).filter(user=self.request.user, user_bought=None).all()


class BuyReferralRequest(APIView):
    """ Request example:
    {
        "hash": asdsadnjsandjsanj
    }
    """
    permission_classes = [SufficientFundsPermission]

    def post(self, request):
        referral_model = get_object_or_404(Referral, hash=request.data['hash'])
        self.check_object_permissions(request, referral_model.price)
        if request.user.wallet > referral_model.price:
            with transaction.atomic():
                referral_model.buy_request(request.user)
                PaymentProcessor.proceed_referral_buy_request(referral_model, request.user)
            return Response(data={'status':'success'}, status=status.HTTP_201_CREATED)
        else:
            return Response(status=status.HTTP_409_CONFLICT)


class BuyReferralRequestDecision(APIView):

    permission_classes = [ObjectOwnerPermission]

    def post(self, request):

        DECISION_ACCEPT = 'accept'
        DECISION_REJECT = 'reject'

        decision = request.data.get('type')
        referral_model = get_object_or_404(Referral, hash=request.data['hash'])
        self.check_object_permissions(request, referral_model)

        if decision == DECISION_ACCEPT:
            referral_model.buy_request_accept()
            PaymentProcessor.proceed_referral_buy_request_accept(referral_model)
            return Response(data={'status':'success'}, status=status.HTTP_201_CREATED)

        elif decision == DECISION_REJECT:
            referral_model.buy_request_reject()
            PaymentProcessor.proceed_referral_buy_request_reject(referral_model)
            return Response(data={'status':'success'}, status=status.HTTP_201_CREATED)

        return Response(status=status.HTTP_409_CONFLICT)


class ReferralFeedbackView(APIView):

    def post(self, request):
        data = request.data.copy()
        referral_model = get_object_or_404(Referral, hash=data['hash'])
        try:
            referral_feedback_model = referral_model.feedback
        except ReferralFeedback.DoesNotExist:
            referral_feedback_model = ReferralFeedback(referral=referral_model)
        serialized = ReferralFeedbackSer(instance=referral_feedback_model, partial=True, data=data)
        if serialized.is_valid():
            serialized.save()
            return Response(serialized.data, status=status.HTTP_201_CREATED)
        return Response(data=serialized._errors, status=status.HTTP_400_BAD_REQUEST)
