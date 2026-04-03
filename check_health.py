import boto3

AWS_REGION = "us-east-1"
elbv2 = boto3.client('elbv2', region_name=AWS_REGION)

def check_tg(tg_name):
    try:
        tgs = elbv2.describe_target_groups(Names=[tg_name])
        tg_arn = tgs['TargetGroups'][0]['TargetGroupArn']
        health = elbv2.describe_target_health(TargetGroupArn=tg_arn)
        print(f"Target Group: {tg_name}")
        for desc in health['TargetHealthDescriptions']:
            print(f"  Target: {desc['Target']['Id']}:{desc['Target']['Port']} Status: {desc['TargetHealth']['State']}")
    except Exception as e:
        print(f"Error checking {tg_name}: {e}")

check_tg("dijkfood-tg-cadastro")
check_tg("dijkfood-tg-rotas")
check_tg("dijkfood-tg-pedidos")
